# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Anekal 2031 plan roads from the Mobility Plan's 1:10,000 grid sheets (open-decisions #72).

  python build_roads.py [--fit E0,N0] [--geojson DIR] [--spotcheck N] [--no-osm]

Stages (all data in %TEMP%\\qnit_planning, deleted after; the cadastral service must be up
for the georeference):
  1. download BMRDA-ANK-MP2031-MOB-10K (sha256 checked).
  2. georeference: the 23 sheets are 6 x 5 km cells of one grid at 1:10,000 (key plan on
     each sheet; map cell 1701 x 1417.3 pt). Two unknowns for the whole grid (E0, N0):
     seeded from village names on the sheets against cadastral village points, refined per
     sheet by matching the sheet's survey-boundary strokes (0.48 pt black) to cadastral
     parcel edges (translation search). QA per sheet: match score against a null of
     1.5-3.5 km shifts, quadrant agreement, survey-number labels landing in the parcel of
     that number.
  3. extract each sheet in a capped worker (roads_mob.py, 2 GB).
  4. georeference, clip to each sheet's own cell, attribute, QA (label vs drawn ROW, ring
     road ROW vs the Zonal Regulations, OSM centrelines inside the plan corridors).
  5. write the text summary to infra/planning/layer_index.json ("roads"; no geometry).
     --geojson writes the features (WGS84) to DIR for publishing (build_tiles.py --roads).
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import datetime as dt
import difflib
import json
import math
import os
import re
import sys
import time
import urllib.request

import numpy as np
import pymupdf
import qnit_fetch as qf
import shapely
import shapely.ops
import winjob
from PIL import Image, ImageDraw, ImageFilter
from pyproj import Transformer
from shapely.geometry import LineString, Point, mapping, shape

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
INDEX = os.path.join(REPO, "infra", "planning", "layer_index.json")
DOCS = os.path.join(REPO, "infra", "planning", "plan_docs.csv")
PLAN_ID = "BMRDA-ANK-MP2031"
DOC_ID = "BMRDA-ANK-MP2031-MOB-10K"
ZR_DOC = "BMRDA-ANK-MP2031-ZR"
CADASTRAL = os.getenv("CADASTRAL_URL", "http://127.0.0.1:8011")
CAD_DIST, CAD_TALUK, CAD_HOBLIS = 20, 3, range(1, 10)  # Anekal taluk
TO_UTM = Transformer.from_crs(4326, 32643, always_xy=True)
TO_WGS = Transformer.from_crs(32643, 4326, always_xy=True)

S = 10000 * 0.0254 / 72  # metres per pt at 1:10,000
BOX = (38.2, 39.4, 1880.3, 1652.5)  # map box incl. the overlap margin
CELL_PT = (108.7, 137.3, 1809.7, 1554.6)  # the sheet's own 6 x 5 km cell
CELL_W, CELL_H = 6000.0, 5000.0
# grid number -> (column, row) on the key plan
CELL = {1: (3, 1), 2: (4, 1), 3: (3, 2), 4: (4, 2), 5: (1, 3), 6: (2, 3), 7: (3, 3),
        8: (0, 4), 9: (1, 4), 10: (2, 4), 11: (3, 4), 12: (0, 5), 13: (1, 5), 14: (2, 5),
        15: (3, 5), 16: (0, 6), 17: (1, 6), 18: (2, 6), 19: (3, 6), 20: (0, 7), 21: (1, 7),
        22: (2, 7), 23: (3, 7)}  # fmt: skip
NULL_SHIFTS = [
    (1500, 0),
    (0, 2000),
    (-2500, 0),
    (0, -3000),
    (2500, 2500),
    (-3500, -1500),
]
WORKER_CAP = 2 * 1024**3
LABEL_PATH_HIGH_M = 200.0

STATUS_TEXT = {
    "to_be_widened": "Existing road to be widened (plan ROW)",
    "proposed": "Proposed road (plan ROW)",
    "existing_row_stated": "Existing road; the plan states its ROW",
    "ring_proposed": "Proposed ring / radial road (plan ROW from the sheet legend)",
    "existing_drawn": "Existing road drawn on the plan (no width label); width as drawn",
}


def log(msg: str) -> None:
    print(msg, flush=True)


def doc_row(doc_id: str) -> dict:
    with open(DOCS, encoding="utf-8") as f:
        return next(r for r in csv.DictReader(f) if r["doc_id"] == doc_id)


def get_json(url: str):
    import urllib.request

    with urllib.request.urlopen(url, timeout=600) as r:
        return json.load(r)


def to_utm_geom(g):
    return shapely.transform(
        g, lambda xy: np.column_stack(TO_UTM.transform(xy[:, 0], xy[:, 1]))
    )


def to_wgs_geom(g):
    return shapely.transform(
        g, lambda xy: np.column_stack(TO_WGS.transform(xy[:, 0], xy[:, 1]))
    )


# --- grid ---------------------------------------------------------------------------------


def grid_no(page) -> int:
    ws = page.get_text("words")
    gw = next(w for w in ws if w[4] == "GRID" and w[1] > 1550)
    y = (gw[1] + gw[3]) / 2
    nums = [
        w
        for w in ws
        if re.fullmatch(r"\d{1,2}", w[4])
        and w[0] > gw[2]
        and abs((w[1] + w[3]) / 2 - y) < 6
    ]
    return int(min(nums, key=lambda w: w[0])[4])


def pt_to_utm(grid: int, e0: float, n0: float):
    col, row = CELL[grid]

    def f(x, y):
        x, y = np.asarray(x, float), np.asarray(y, float)
        return e0 + col * CELL_W + (x - CELL_PT[0]) * S, n0 - (row - 1) * CELL_H - (
            y - CELL_PT[1]
        ) * S

    return f


def cell_poly(grid: int, e0: float, n0: float):
    f = pt_to_utm(grid, e0, n0)
    x0, y1 = f(CELL_PT[0], CELL_PT[3])
    x1, y0 = f(CELL_PT[2], CELL_PT[1])
    return shapely.box(float(x0), float(y1), float(x1), float(y0))


# --- georeference -------------------------------------------------------------------------


def norm_name(t: str) -> str:
    return re.sub(r"[^a-z]", "", t.lower())


def cadastral_villages():
    out = []
    for h in CAD_HOBLIS:
        fc = get_json(
            f"{CADASTRAL}/boundaries?dist={CAD_DIST}&taluk={CAD_TALUK}&hobli={h}"
        )
        for f in fc["features"]:
            if f.get("geometry"):
                out.append(
                    (
                        h,
                        f["properties"]["village_code"],
                        f["properties"].get("village_name") or "",
                        to_utm_geom(shape(f["geometry"])),
                    )
                )
    return out


def sheet_village_names(page):
    lines = []
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            if not ln["spans"] or f"#{ln['spans'][0]['color']:06x}" != "#6b6b6c":
                continue
            t = "".join(s["text"] for s in ln["spans"]).replace(" ", "")
            x0, y0, x1, y1 = ln["bbox"]
            if (
                BOX[0] < x0 < BOX[2]
                and BOX[1] < y0 < BOX[3]
                and re.fullmatch(r"[A-Za-z/().-]{3,}", t)
            ):
                lines.append((t, (x0 + x1) / 2, (y0 + y1) / 2))
    out = list(lines)
    for a in lines:  # two-line names
        for b in lines:
            if a is not b and 3 < b[2] - a[2] < 14 and abs(b[1] - a[1]) < 40:
                out.append((a[0] + b[0], (a[1] + b[1]) / 2, (a[2] + b[2]) / 2))
    return out


def seed_fit(doc, villages):
    """(E0, N0) from village names on the sheets against cadastral village points."""
    names: dict[str, list] = {}
    for _h, _v, nm, g in villages:
        p = g.representative_point()
        names.setdefault(norm_name(nm), []).append((p.x, p.y))
    keys = list(names)
    obs = []
    for page in doc:
        g = grid_no(page)
        col, row = CELL[g]
        for t, x, y in sheet_village_names(page):
            best = difflib.get_close_matches(norm_name(t), keys, n=1, cutoff=0.88)
            if best and len(names[best[0]]) == 1:
                vx, vy = names[best[0]][0]
                obs.append(
                    (
                        vx - col * CELL_W - (x - CELL_PT[0]) * S,
                        vy + (row - 1) * CELL_H + (y - CELL_PT[1]) * S,
                    )
                )
    a = np.array(obs)
    med = np.median(a, axis=0)
    ok = np.hypot(*(a - med).T) < 1000
    e0, n0 = a[ok].mean(axis=0)
    log(
        f"  seed: {len(obs)} village names, {int(ok.sum())} within 1 km -> E0 {e0:.1f} N0 {n0:.1f}"
    )
    return (
        float(e0),
        float(n0),
        {"village_names": len(obs), "inliers_1km": int(ok.sum())},
    )


def _strokes(page):
    pts = []
    for d in page.get_drawings():
        c = d.get("color")
        if (
            d["type"] != "s"
            or c is None
            or any(v > 0.01 for v in c[:3])
            or round(d.get("width") or 0, 2) != 0.48
        ):
            continue
        for it in d["items"]:
            if it[0] == "l":
                a, b = it[1], it[2]
            elif it[0] == "c":
                a, b = it[1], it[4]
            else:
                continue
            if BOX[0] < a.x < BOX[2] and BOX[1] < a.y < BOX[3]:
                n = max(1, int(math.hypot(b.x - a.x, b.y - a.y) / 3))
                t = np.linspace(0, 1, n + 1)
                pts.append(
                    np.column_stack([a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t])
                )
    return np.vstack(pts) if pts else np.zeros((0, 2))


def _survey_labels(page):
    return [
        ((w[0] + w[2]) / 2, (w[1] + w[3]) / 2, w[4])
        for w in page.get_text("words")
        if re.fullmatch(r"\d{1,3}", w[4])
        and BOX[0] < w[0] < BOX[2]
        and BOX[1] < w[1] < BOX[3]
        and (w[3] - w[1]) < 9
    ]


def _edge_raster(rings, bbox, res, rad):
    ox, oy = bbox[0], bbox[3]
    w, h = int((bbox[2] - bbox[0]) / res) + 1, int((bbox[3] - bbox[1]) / res) + 1
    im = Image.new("L", (w, h), 0)
    dr = ImageDraw.Draw(im)
    for ls in rings:
        dr.line(
            [((x - ox) / res, (oy - y) / res) for x, y in np.asarray(ls.coords)],
            fill=1,
            width=1,
        )
    k = 2 * round(rad / res) + 1
    if k > 1:
        im = im.filter(ImageFilter.MaxFilter(k))
    return np.asarray(im, dtype=np.uint8), ox, oy


def _score(m, ox, oy, res, e, n, dx, dy):
    c = ((e + dx - ox) / res).astype(np.int64)
    r = ((oy - (n + dy)) / res).astype(np.int64)
    ok = (c >= 0) & (r >= 0) & (c < m.shape[1]) & (r < m.shape[0])
    return float(m[r[ok], c[ok]].sum()) / max(1, len(e))


def refine_sheet(page, grid, e0, n0, villages):
    """Translation of one sheet against cadastral parcel edges, with QA."""
    f = pt_to_utm(grid, e0, n0)
    p = _strokes(page)
    if len(p) < 500:
        return {"grid": grid, "skipped": f"{len(p)} survey-boundary points"}
    if len(p) > 40000:
        p = p[np.random.default_rng(grid).choice(len(p), 40000, replace=False)]
    e, n = f(p[:, 0], p[:, 1])
    x0, y1 = f(BOX[0], BOX[3])
    x1, y0 = f(BOX[2], BOX[1])
    area = shapely.box(float(x0), float(y1), float(x1), float(y0)).buffer(700)
    vs = [(h, v) for h, v, _nm, g in villages if g.intersects(area)]
    parcels = []
    for h, v in vs:
        for ft in get_json(
            f"{CADASTRAL}/data?dist={CAD_DIST}&taluk={CAD_TALUK}&hobli={h}&vlg={v}"
        )["features"]:
            if ft.get("geometry"):
                parcels.append(
                    (
                        to_utm_geom(shape(ft["geometry"])),
                        str(ft["properties"].get("survey_no") or "")
                        .split("/")[0]
                        .strip(),
                    )
                )
    if not parcels:
        return {"grid": grid, "skipped": "no cadastral villages under the sheet"}
    geoms = [g for g, _ in parcels]
    rings = [
        poly.exterior
        for g in geoms
        for poly in getattr(g, "geoms", [g])
        if poly.geom_type == "Polygon"
    ]
    bb = area.bounds
    m4, ox, oy = _edge_raster(rings, bb, 4.0, 12.0)
    sub = np.random.default_rng(1).choice(len(e), min(8000, len(e)), replace=False)
    coarse = max(
        (_score(m4, ox, oy, 4.0, e[sub], n[sub], dx, dy), dx, dy)
        for dx in range(-300, 301, 8)
        for dy in range(-300, 301, 8)
    )
    del m4
    m1, ox, oy = _edge_raster(rings, bb, 1.0, 2.0)
    fine = max(
        (_score(m1, ox, oy, 1.0, e, n, dx, dy), dx, dy)
        for dx in range(coarse[1] - 12, coarse[1] + 13)
        for dy in range(coarse[2] - 12, coarse[2] + 13)
    )
    null = float(
        np.mean(
            [
                _score(m1, ox, oy, 1.0, e, n, fine[1] + a, fine[2] + b)
                for a, b in NULL_SHIFTS
            ]
        )
    )
    cx, cy = (BOX[0] + BOX[2]) / 2, (BOX[1] + BOX[3]) / 2
    quads = {}
    for q, sel in {
        "NW": (p[:, 0] < cx) & (p[:, 1] < cy),
        "NE": (p[:, 0] >= cx) & (p[:, 1] < cy),
        "SW": (p[:, 0] < cx) & (p[:, 1] >= cy),
        "SE": (p[:, 0] >= cx) & (p[:, 1] >= cy),
    }.items():
        if sel.sum() >= 1000:
            b = max(
                (_score(m1, ox, oy, 1.0, e[sel], n[sel], dx, dy), dx, dy)
                for dx in range(fine[1] - 8, fine[1] + 9)
                for dy in range(fine[2] - 8, fine[2] + 9)
            )
            quads[q] = [b[1], b[2]]
    del m1
    tree = shapely.STRtree(geoms)
    labs = _survey_labels(page)

    def hits(dx, dy):
        if not labs:
            return 0, 0
        lx, ly = f(np.array([lb[0] for lb in labs]), np.array([lb[1] for lb in labs]))
        li, gi = tree.query(shapely.points(lx + dx, ly + dy), predicate="within")
        inside = {int(a) for a in li}
        same = {
            int(a)
            for a, b in zip(li, gi, strict=True)
            if parcels[int(b)][1] == labs[int(a)][2]
        }
        return len(inside), len(same)

    ins, same = hits(fine[1], fine[2])
    nul = float(np.mean([hits(fine[1] + a, fine[2] + b)[1] for a, b in NULL_SHIFTS]))
    spread = max(
        (math.hypot(a - fine[1], b - fine[2]) for a, b in quads.values()), default=0.0
    )
    return {
        "grid": grid,
        "shift_m": [fine[1], fine[2]],
        "edge_match": round(fine[0], 3),
        "edge_null": round(null, 3),
        "edge_ratio": round(fine[0] / max(null, 0.01), 1),
        "quadrant_spread_m": round(spread, 1),
        "survey_labels": len(labs),
        "labels_in_parcel": ins,
        "labels_same_number": same,
        "labels_same_number_null": round(nul, 1),
        "villages": len(vs),
        "parcels": len(parcels),
    }


def georeference(doc, fit_arg, pages):
    villages = cadastral_villages()
    log(f"  cadastral: {len(villages)} villages (dist {CAD_DIST}, taluk {CAD_TALUK})")
    if fit_arg:
        e0, n0 = (float(v) for v in fit_arg.split(","))
        seed = {"given": True}
    else:
        e0, n0, seed = seed_fit(doc, villages)
    sheets = []
    for k in pages:
        page = doc[k]
        g = grid_no(page)
        r = refine_sheet(page, g, e0, n0, villages)
        sheets.append(r)
        log(f"  grid {g}: {json.dumps(r)}")
    ok = [r for r in sheets if "shift_m" in r and r["edge_ratio"] >= 3.0]
    w = np.array([r["parcels"] for r in ok], float)
    sh = np.array([r["shift_m"] for r in ok], float)
    dx, dy = np.median(sh, axis=0)
    e0f, n0f = e0 + float(dx), n0 + float(dy)
    resid = np.hypot(sh[:, 0] - dx, sh[:, 1] - dy)
    fit = {
        "method": "grid_fit_cadastral_edges",
        "basis": "23 sheets = 6 x 5 km cells of one 1:10,000 grid (key plan); E0/N0 seeded from "
        "village names, refined per sheet on cadastral parcel edges; the median sheet shift applied",
        "e0": round(e0f, 1),
        "n0": round(n0f, 1),
        "m_per_pt": round(S, 6),
        "cell_m": [CELL_W, CELL_H],
        "cell_pt": list(CELL_PT),
        "crs": "EPSG:32643",
        "seed": seed,
        "sheets_used": len(ok),
        "sheets_checked": len(sheets),
        "sheet_residual_m": {
            "median": round(float(np.median(resid)), 1),
            "max": round(float(resid.max()), 1),
        },
        "weights_parcels": int(w.sum()),
        "relative_to": "cadastral parcels (the sheets are drawn on the survey maps); see osm_check for the "
        "offset from OSM",
    }
    log(
        f"  fit: E0 {e0f:.1f} N0 {n0f:.1f}; sheet residual median {fit['sheet_residual_m']['median']} m, "
        f"max {fit['sheet_residual_m']['max']} m"
    )
    return fit, sheets


# --- extraction ---------------------------------------------------------------------------


def run_worker(path: str, page: int, area: str):
    job = {"source_path": path, "page": page, "box": list(BOX)}
    rc, out, err, peak = winjob.run_capped(
        [sys.executable, "-X", "faulthandler", os.path.join(HERE, "roads_mob.py")],
        json.dumps(job).encode(),
        WORKER_CAP,
        cwd=HERE,
    )
    if rc != 0:
        raise RuntimeError(
            f"roads worker page {page}: rc {rc}: {err.decode(errors='replace')[-800:]}"
        )
    return json.loads(out), peak


# --- assembly -----------------------------------------------------------------------------


BAND_MIN_M, BAND_MAX_M, BAND_IQR_M, BAND_MIN_N = 3.0, 30.0, 2.0, 3


def drawn_band(ed: dict) -> tuple[float | None, str]:
    """Width of the grey road band as drawn (median over the stations, to 0.5 m) and its
    confidence: MEDIUM when >= 3 stations agree within 2 m (IQR) and it is 3-30 m."""
    gw, iqr, n = ed.get("gw"), ed.get("gw_iqr"), ed.get("gw_n") or 0
    if not gw:
        return None, "LOW"
    ok = (
        n >= BAND_MIN_N
        and iqr
        and iqr[1] - iqr[0] <= BAND_IQR_M
        and BAND_MIN_M <= gw <= BAND_MAX_M
    )
    return round(gw * 2) / 2, "MEDIUM" if ok else "LOW"


def assemble(doc, results, fit):
    e0, n0 = fit["e0"], fit["n0"]
    feats, rings_by_class, names_all, labels_all = [], {}, [], []
    per_sheet = []
    small_low_km = [0.0]  # unlabelled existing roads left out (band width inconsistent)
    for page_no, res in results.items():
        g = grid_no(doc[page_no])
        f = pt_to_utm(g, e0, n0)
        cell = cell_poly(g, e0, n0)
        labels = res["labels"]
        for lb in labels:
            x, y = f(lb["x"], lb["y"])
            lb_u = {**lb, "e": float(x), "n": float(y), "grid": g}
            if cell.contains(Point(x, y)):
                labels_all.append(lb_u)
        for nm in res["names"]:
            x, y = f(nm["x"], nm["y"])
            names_all.append({"name": nm["name"], "e": float(x), "n": float(y)})
        km = 0.0
        for ed in res["edges"]:
            c = np.asarray(ed["pt"])
            x, y = f(c[:, 0], c[:, 1])
            line = LineString(np.column_stack([x, y])).intersection(cell)
            if line.is_empty or line.length < 3:
                continue
            lb = labels[ed["label_id"]] if ed["label_id"] is not None else None
            kind = lb["kind"] if lb else None
            band_m, band_conf = drawn_band(ed)
            if kind == "bare":
                status = "existing_row_stated"
            elif lb or ed["w"] is not None:
                status = "to_be_widened" if ed["grey"] >= 0.5 else "proposed"
            else:
                status = (
                    "existing_drawn"  # unlabelled existing road, width as drawn (#78)
                )
            if lb and ed["w"] is not None:
                source, conf = "label_and_drawn", "HIGH"
            elif lb:
                source = "label"
                conf = "HIGH" if ed["label_path_m"] <= LABEL_PATH_HIGH_M else "MEDIUM"
            elif ed["w"] is not None:
                source, conf = "drawn", "MEDIUM"
            else:
                source, conf = "drawn_band", band_conf
            if status == "existing_drawn":
                if band_conf != "MEDIUM":
                    small_low_km[0] += line.length / 1000
                    continue  # band width not consistent along the road: no width to show
                row = band_m
            elif not lb and (ed["w"] < 6.0 or ed["len_m"] < 40.0):
                continue  # drawn-only noise: too narrow for a road or too short to judge
            else:
                row = float(ed["label"]) if lb else round(ed["w"] * 2) / 2
            feats.append(
                {
                    "geom": line,
                    "row_m": row,
                    "status": status,
                    "width_source": source,
                    "confidence": conf,
                    "drawn_row_m": round(ed["w"], 1) if ed["w"] is not None else None,
                    # the grey band = the existing road as drawn (MEDIUM only)
                    "drawn_band_m": band_m if band_conf == "MEDIUM" else None,
                    "label_m": ed["label"],
                    "label_path_m": ed["label_path_m"],
                    "grid": g,
                }
            )
            km += line.length / 1000
        for rg in res["rings"]:
            for (ax, ay), (bx, by) in rg["segments"]:
                x, y = f([ax, bx], [ay, by])
                seg = LineString([(x[0], y[0]), (x[1], y[1])]).intersection(cell)
                if not seg.is_empty:
                    rings_by_class.setdefault((rg["class"], rg["row_m"]), []).append(
                        seg
                    )
        per_sheet.append(
            {
                "grid": g,
                "page": page_no + 1,
                "edges_km": round(km, 2),
                "labels": len(labels),
                "worker_s": res["secs"],
            }
        )
    # name the roads: edges within 60 m of a road-name label
    if names_all and feats:
        tree = shapely.STRtree([ft["geom"] for ft in feats])
        for nm in names_all:
            for i in tree.query(
                Point(nm["e"], nm["n"]), predicate="dwithin", distance=60.0
            ):
                feats[int(i)].setdefault("road_name", nm["name"])
    rings = []
    for (cls, row), segs in rings_by_class.items():
        u = shapely.union_all([s.buffer(row / 2, cap_style="flat") for s in segs])
        # dashes leave gaps shorter than the ROW: close them
        u = u.buffer(20.0).buffer(-20.0)
        rings.append(
            {
                "class": cls,
                "row_m": float(row),
                "corridor": u,
                "dashes": len(segs),
                "centre_km": round(sum(s.length for s in segs) / 1000, 1),
            }
        )
    return feats, rings, labels_all, per_sheet, round(small_low_km[0], 1)


def merge_features(feats):
    """Join touching edges with the same attributes into longer lines; drawn widths are
    the median of the edges that make up each merged line."""
    groups: dict[tuple, list] = {}
    for ft in feats:
        key = (
            ft["row_m"],
            ft["status"],
            ft["width_source"],
            ft["confidence"],
            ft.get("road_name"),
        )
        groups.setdefault(key, []).append(ft)
    out = []
    for key, fs in groups.items():
        geoms = [f_["geom"] for f_ in fs]
        tree = shapely.STRtree(geoms)
        merged = shapely.line_merge(shapely.union_all(geoms))
        for p in getattr(merged, "geoms", [merged]):
            if p.length < 5:
                continue
            idx = [int(k) for k in tree.query(p.buffer(0.5), predicate="intersects")]
            mine = [fs[k] for k in idx if fs[k]["geom"].within(p.buffer(0.5))] or [
                fs[k] for k in idx
            ]

            def med(field, mine=mine):
                vals = [f_[field] for f_ in mine if f_.get(field) is not None]
                return round(float(np.median(vals)), 1) if vals else None

            out.append(
                {
                    "geom": p,
                    "row_m": key[0],
                    "status": key[1],
                    "width_source": key[2],
                    "confidence": key[3],
                    "road_name": key[4],
                    "drawn_row_m_median": med("drawn_row_m"),
                    "drawn_band_m": med("drawn_band_m"),
                }
            )
    return out


# --- QA -----------------------------------------------------------------------------------


ZR_CLASS = {"STRR": "STRR", "IRR": "IRR", "ITRR": "ITRR", "RR": "Radial road"}


def zr_ring_check(area: str):
    """Table 'Proposed Building Line' of the Zonal Regulations: ROW and building line of the
    ring / radial roads ('1 STRR 90.0 6.0 ... 4 RR 60 6.0')."""
    r = doc_row(ZR_DOC)
    path, _ = qf.download(r["source_url"], area, "zr.pdf", r["sha256"])
    doc = pymupdf.open(path)
    found = []
    for i, page in enumerate(doc):
        t = re.sub(r"\s+", " ", page.get_text())
        if "Proposed Building Line" not in t:
            continue
        for m in re.finditer(
            r"\b\d\s+(STRR|ITRR|IRR|RR)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)", t
        ):
            found.append(
                {
                    "page": i + 1,
                    "road": ZR_CLASS[m.group(1)],
                    "row_m": float(m.group(2)),
                    "building_line_m": float(m.group(3)),
                }
            )
    doc.close()
    return found


def band_calibration(feats) -> dict:
    """The drawn grey band against the label, for existing roads whose ROW the plan states
    (bare labels): if the sheets draw the band to scale, it matches the label."""
    by = {}
    for f_ in feats:
        if f_["status"] == "existing_row_stated" and f_.get("drawn_band_m") is not None:
            by.setdefault(f_["row_m"], []).append(
                (f_["drawn_band_m"], f_["geom"].length)
            )
    out, allw, alld = {}, [], []
    for v, rows in sorted(by.items()):
        d = np.array([b - v for b, _ in rows])
        w = np.array([ln for _, ln in rows])
        out[f"{v:g}"] = {
            "pieces": len(rows),
            "km": round(float(w.sum()) / 1000, 1),
            "band_median_m": round(float(np.median([b for b, _ in rows])), 1),
            "within_2m": round(float(np.mean(np.abs(d) <= 2.0)), 3),
        }
        allw.extend(w.tolist())
        alld.extend(d.tolist())
    alld_a = np.array(alld)
    return {
        "by_label_m": out,
        "pieces": len(alld),
        "within_2m": round(float(np.mean(np.abs(alld_a) <= 2.0)), 3)
        if len(alld)
        else None,
        "within_2m_by_length": round(
            float(np.average(np.abs(alld_a) <= 2.0, weights=allw)), 3
        )
        if len(alld)
        else None,
    }


def strip_check(feats, villages, n_max=400) -> dict:
    """Independent check of the drawn band: where the plan centreline runs inside a cadastral
    survey-0 strip (the village's road land), the strip's width across the line against the
    band width."""
    cands = [
        f_
        for f_ in feats
        if f_.get("drawn_band_m") is not None and f_["geom"].length >= 40
    ]
    rng = np.random.default_rng(2031)
    if len(cands) > n_max * 3:
        cands = [cands[k] for k in rng.choice(len(cands), n_max * 3, replace=False)]
    vtree = shapely.STRtree([v[3] for v in villages])
    cache: dict = {}
    pairs = []
    for f_ in cands:
        if len(pairs) >= n_max:
            break
        ln = f_["geom"]
        mid = ln.interpolate(0.5, normalized=True)
        vi = vtree.query(mid, predicate="within")
        if not len(vi):
            continue
        h, v = villages[int(vi[0])][0], villages[int(vi[0])][1]
        if (h, v) not in cache:
            fc = get_json(
                f"{CADASTRAL}/data?dist={CAD_DIST}&taluk={CAD_TALUK}&hobli={h}&vlg={v}"
            )
            strips = [
                shapely.make_valid(to_utm_geom(shape(x["geometry"])))
                for x in fc["features"]
                if x.get("geometry")
                and str(x["properties"].get("survey_no") or "").split("/")[0].strip()
                == "0"
            ]
            cache[(h, v)] = (strips, shapely.STRtree(strips) if strips else None)
        strips, tree = cache[(h, v)]
        if tree is None:
            continue
        hit = tree.query(mid, predicate="within")
        if not len(hit):
            continue
        strip = strips[int(hit[0])]
        d = ln.project(mid)
        a, b = ln.interpolate(max(0.0, d - 3)), ln.interpolate(min(ln.length, d + 3))
        tx, ty = b.x - a.x, b.y - a.y
        tl = float(np.hypot(tx, ty))
        if not tl:
            continue
        nx, ny = -ty / tl, tx / tl
        cross = LineString(
            [(mid.x - nx * 60, mid.y - ny * 60), (mid.x + nx * 60, mid.y + ny * 60)]
        )
        inter = cross.intersection(strip)
        seg = next(
            (
                g
                for g in getattr(inter, "geoms", [inter])
                if g.geom_type == "LineString" and g.distance(mid) < 0.5
            ),
            None,
        )
        if seg is None or seg.length > 60:
            continue
        pairs.append((f_["drawn_band_m"], seg.length, f_["status"]))
    if not pairs:
        return {"pairs": 0}
    d = np.array([b - s_ for b, s_, _ in pairs])
    return {
        "pairs": len(pairs),
        "median_abs_diff_m": round(float(np.median(np.abs(d))), 1),
        "median_diff_m": round(float(np.median(d)), 1),
        "within_2m": round(float(np.mean(np.abs(d) <= 2.0)), 3),
        "within_3m": round(float(np.mean(np.abs(d) <= 3.0)), 3),
        "by_status": {
            st: round(
                float(np.median(np.abs([b - s_ for b, s_, t in pairs if t == st]))), 1
            )
            for st in sorted({t for _, _, t in pairs})
        },
    }


def osm_check(feats, rings):
    """OSM centrelines of public roads against the plan's existing-road corridors: share of
    OSM sample points (every 10 m) inside, against the same corridors shifted 1.5-3.5 km, and
    the median offset of OSM points from the nearest plan centreline within 30 m."""
    import build_layer_index as bli

    ex = [
        f_ for f_ in feats if f_["status"] in ("existing_row_stated", "to_be_widened")
    ]
    if not ex:
        return None
    corr = shapely.union_all(
        [f_["geom"].buffer(f_["row_m"] / 2, cap_style="flat") for f_ in ex]
    )
    cent = shapely.union_all([f_["geom"] for f_ in ex])
    b = corr.bounds
    w, s_ = TO_WGS.transform(b[0], b[1])
    e, n = TO_WGS.transform(b[2], b[3])
    els = bli.overpass_roads((w, s_, e, n), "motorway|trunk|primary|secondary|tertiary")
    pts = []
    for el in els:
        geo = el.get("geometry") or []
        if len(geo) < 2:
            continue
        xs, ys = TO_UTM.transform([g["lon"] for g in geo], [g["lat"] for g in geo])
        ln = LineString(np.column_stack([xs, ys]))
        k = max(2, int(ln.length / 10))
        pts.extend(
            (ln.interpolate(i / k, normalized=True).coords[0]) for i in range(k + 1)
        )
    if not pts:
        return {"osm_points": 0}
    P = np.array(pts)
    near = shapely.dwithin(cent, shapely.points(P), 60.0)
    P = P[near]
    if not len(P):
        return {"osm_points": 0}
    pp = shapely.points(P)
    inside = float(shapely.contains_xy(corr, P[:, 0], P[:, 1]).mean())
    null = float(
        np.mean(
            [
                shapely.contains_xy(corr, P[:, 0] + a, P[:, 1] + b2).mean()
                for a, b2 in NULL_SHIFTS
            ]
        )
    )
    d = shapely.distance(cent, pp)
    d30 = d[d <= 30]
    # signed systematic offset: nearest-point vectors
    vec = []
    for p in pp[d <= 30][:5000]:
        q = shapely.ops.nearest_points(cent, p)[0]
        vec.append((p.x - q.x, p.y - q.y))
    vec = np.array(vec) if vec else np.zeros((0, 2))
    return {
        "osm_ways": len(els),
        "osm_points_near_plan": len(P),
        "inside_corridor": round(inside, 3),
        "inside_null": round(null, 3),
        "ratio": round(inside / max(null, 0.01), 1),
        "median_dist_m": round(float(np.median(d30)), 1) if len(d30) else None,
        "p90_dist_m": round(float(np.percentile(d30, 90)), 1) if len(d30) else None,
        "mean_offset_m": [round(float(v), 1) for v in vec.mean(axis=0)]
        if len(vec)
        else None,
    }


def spotcheck(doc, results, fit, n, out_png):
    """n random labelled edges: source crop with the extracted centreline and corridor."""
    rng = np.random.default_rng(2031)
    cands = [
        (p, i)
        for p, res in results.items()
        for i, ed in enumerate(res["edges"])
        if ed["label"] and ed["len_m"] > 60
    ]
    pick = [cands[k] for k in rng.choice(len(cands), min(n, len(cands)), replace=False)]
    tiles = []
    for p, i in pick:
        ed = results[p]["edges"][i]
        c = np.asarray(ed["pt"])
        cx, cy = c[len(c) // 2]
        r = 40.0
        clip = pymupdf.Rect(cx - r, cy - r, cx + r, cy + r)
        pm = doc[p].get_pixmap(dpi=200, clip=clip, alpha=False)
        im = Image.frombytes("RGB", (pm.width, pm.height), pm.samples)
        dr = ImageDraw.Draw(im)
        k = pm.width / (2 * r)
        half = ed["label"] / 2 / S * k
        xy = [((x - clip.x0) * k, (y - clip.y0) * k) for x, y in c]
        dr.line(xy, fill=(0, 90, 255), width=2)
        ln = LineString(xy)
        for side in (half, -half):
            try:
                off = ln.offset_curve(side)
                dr.line(list(off.coords), fill=(0, 200, 0), width=1)
            except Exception as ex:  # noqa: BLE001 - an odd curve: skip its edge lines
                log(f"  spotcheck: no edge lines ({ex})")
        dr.text(
            (3, 3),
            f"g{grid_no(doc[p])} {ed['label']}m drawn {ed['w'] and round(ed['w'], 1)}",
            fill=(255, 0, 0),
        )
        tiles.append(im.resize((300, 300)))
    cols = 5
    rows = math.ceil(len(tiles) / cols)
    sheet = Image.new("RGB", (cols * 300, rows * 300), "white")
    for k, t in enumerate(tiles):
        sheet.paste(t, ((k % cols) * 300, (k // cols) * 300))
    sheet.save(out_png)


# --- main ---------------------------------------------------------------------------------


def _rounded(geom_json, nd=6):
    """Coordinates to 6 decimals (~0.1 m): a third of the file size, no visible change."""

    def r(c):
        if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
            return [round(c[0], nd), round(c[1], nd)]
        return [r(x) for x in c]

    return {"type": geom_json["type"], "coordinates": r(geom_json["coordinates"])}


def geojson_features(feats, rings, labels):
    """Corridors (ROW polygons, for the map and the parcel answer), centrelines (the parcel
    answer's existing-width estimate) and width labels, WGS84. `rid` pairs a corridor with
    its centreline; ring / radial roads are corridors only."""
    fs = []
    keep = (
        "row_m",
        "status",
        "width_source",
        "confidence",
        "road_name",
        "drawn_row_m_median",
        "drawn_band_m",
    )
    for rid, f_ in enumerate(feats):
        props = {k: f_.get(k) for k in keep}
        props["rid"] = rid
        corridor = f_["geom"].buffer(f_["row_m"] / 2, cap_style="flat").simplify(0.5)
        fs.append(
            {
                "type": "Feature",
                "geometry": _rounded(mapping(to_wgs_geom(f_["geom"].simplify(0.5)))),
                "properties": {**props, "part": "centreline"},
            }
        )
        fs.append(
            {
                "type": "Feature",
                "geometry": _rounded(mapping(to_wgs_geom(corridor))),
                "properties": {**props, "part": "corridor"},
            }
        )
    for rg in rings:
        fs.append(
            {
                "type": "Feature",
                "geometry": _rounded(
                    mapping(to_wgs_geom(rg["corridor"].simplify(1.0)))
                ),
                "properties": {
                    "part": "corridor",
                    "road_name": rg["class"],
                    "row_m": rg["row_m"],
                    "status": "ring_proposed",
                    "width_source": "legend",
                    "confidence": "HIGH",
                },
            }
        )
    for lb in labels:
        x, y = TO_WGS.transform(lb["e"], lb["n"])
        fs.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [round(x, 6), round(y, 6)],
                },
                "properties": {"part": "label", "row_m": lb["value"]},
            }
        )
    return {"type": "FeatureCollection", "features": fs}


def write_geojson(path, feats, rings, labels):
    fc = geojson_features(feats, rings, labels)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(fc, f, separators=(",", ":"))
    return len(fc["features"])


def publish(
    path: str, n_features: int, src: dict, plan_id: str = PLAN_ID, doc_id: str = DOC_ID
) -> dict:
    """Upload the roads file next to the map tiles (public bucket `planning-tiles`, an
    immutable name per build) and record it in manifest.json under "roads" (the tile
    entries under "plans" are kept). Service-role key from the gitignored
    apps/web/.env.local, never printed."""
    import build_tiles as bt

    st = bt.Storage()
    import gzip

    with open(path, "rb") as f:
        raw = f.read()
    # gzip: GeoJSON compresses ~5x; the browser and the service decompress it
    data = gzip.compress(raw, 9)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    obj = f"roads/{plan_id}.{stamp}.geojson.gz"
    st.put(obj, data, "application/gzip", "public, max-age=31536000, immutable")
    try:
        with urllib.request.urlopen(
            f"{st.public('manifest.json')}?v={stamp}", timeout=60
        ) as r:
            man = json.load(r)
    except Exception as ex:  # noqa: BLE001 - no manifest yet
        log(f"  no manifest to merge ({str(ex)[:80]})")
        man = {"plans": {}}
    entry = {
        "url": st.public(obj),
        "encoding": "gzip",
        "bytes_uncompressed": len(raw),
        "bytes": len(data),
        "features": n_features,
        "doc_id": doc_id,
        "doc_status": src["status"],
        "built_at": stamp,
    }
    man.setdefault("roads", {})[plan_id] = entry
    st.put(
        "manifest.json",
        json.dumps(man, indent=1).encode(),
        "application/json",
        "no-cache",
    )
    log(
        f"  published {obj} ({len(data) / 1e6:.1f} MB gzip, {len(raw) / 1e6:.1f} MB raw) and manifest.json"
    )
    return entry


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--fit",
        default=None,
        help="E0,N0 (skip the seed; the per-sheet refine still runs)",
    )
    ap.add_argument(
        "--geojson",
        default=None,
        help="directory for the features (temp; for publishing)",
    )
    ap.add_argument("--spotcheck", type=int, default=0)
    ap.add_argument(
        "--no-osm",
        action="store_true",
        help="skip the OSM check (the previous result is kept when the source is unchanged)",
    )
    ap.add_argument(
        "--publish", action="store_true", help="upload the roads file (needs --geojson)"
    )
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument(
        "--pages", default=None, help="comma list of 1-based pages (test runs)"
    )
    args = ap.parse_args()
    t0 = time.time()
    src = doc_row(DOC_ID)
    with qf.TempArea("build/roads_ank") as area:
        path, _ = qf.download(src["source_url"], area, "mob10k.pdf", src["sha256"])
        doc = pymupdf.open(path)
        log("georeference")
        pages = (
            [int(p) - 1 for p in args.pages.split(",")]
            if args.pages
            else list(range(len(doc)))
        )
        fit, sheets_qa = georeference(doc, args.fit, pages)
        log(f"extract {len(pages)} sheets")
        results, peaks = {}, []
        with cf.ThreadPoolExecutor(args.workers) as ex:
            futs = {ex.submit(run_worker, path, p, area): p for p in pages}
            for fu in cf.as_completed(futs):
                res, peak = fu.result()
                results[futs[fu]] = res
                peaks.append(peak)
                log(
                    f"  page {futs[fu] + 1}: {len(res['edges'])} edges, {res['secs']} s, peak {peak / 1e6:.0f} MB"
                )
        feats, rings, labels, per_sheet, small_low_km = assemble(doc, results, fit)
        merged = merge_features(feats)
        km = lambda fs: round(sum(f_["geom"].length for f_ in fs) / 1000, 1)
        by_row = {}
        for f_ in merged:
            by_row.setdefault(f_["row_m"], 0.0)
            by_row[f_["row_m"]] += f_["geom"].length / 1000
        by_status = {}
        for f_ in merged:
            by_status[f_["status"]] = (
                by_status.get(f_["status"], 0.0) + f_["geom"].length / 1000
            )
        by_conf = {}
        for f_ in merged:
            by_conf[f_["confidence"]] = (
                by_conf.get(f_["confidence"], 0.0) + f_["geom"].length / 1000
            )
        diffs = [
            f_["drawn_row_m"] - f_["label_m"]
            for f_ in feats
            if f_["drawn_row_m"] is not None and f_["label_m"]
        ]
        diffs_km = [
            f_["geom"].length
            for f_ in feats
            if f_["drawn_row_m"] is not None and f_["label_m"]
        ]
        label_check = {
            "edges": len(diffs),
            "km": round(sum(diffs_km) / 1000, 1),
            "drawn_minus_label_m": {
                q: round(float(np.percentile(diffs, p)), 2)
                for q, p in (("p10", 10), ("median", 50), ("p90", 90))
            }
            if diffs
            else None,
            "within_1m": round(float(np.mean(np.abs(diffs) <= 1.0)), 3)
            if diffs
            else None,
            "within_2m": round(float(np.mean(np.abs(diffs) <= 2.0)), 3)
            if diffs
            else None,
        }
        log("QA")
        zr = zr_ring_check(area)
        ring_check = []
        for rg in rings:
            z = next((z for z in zr if z["road"] == rg["class"]), None)
            ring_check.append(
                {
                    "class": rg["class"],
                    "legend_row_m": rg["row_m"],
                    "zr_row_m": z["row_m"] if z else None,
                    "zr_building_line_m": z["building_line_m"] if z else None,
                    "zr_page": z["page"] if z else None,
                    "agrees": (z["row_m"] == rg["row_m"]) if z else None,
                    "centre_km": rg["centre_km"],
                }
            )
        published = None
        osm = None
        if args.no_osm:
            try:
                with open(INDEX, encoding="utf-8") as f:
                    prev = json.load(f).get("roads", {}).get(PLAN_ID) or {}
                if prev.get("sha256") == src["sha256"] and (prev.get("qa") or {}).get(
                    "osm_check"
                ):
                    osm = {
                        **prev["qa"]["osm_check"],
                        "from_build": prev.get("built_at"),
                    }
            except (OSError, ValueError):
                osm = None
        else:
            try:
                osm = osm_check(merged, rings)
            except Exception as ex:  # noqa: BLE001 - Overpass down: the build still lands
                osm = {"pending": f"Overpass unavailable ({ex}); re-run the build"}
                log(f"  OSM check skipped: {ex}")
        band_cal = band_calibration(merged)
        log(f"  band calibration: {json.dumps(band_cal)}")
        strips = strip_check(merged, cadastral_villages())
        log(f"  survey-0 strip check: {json.dumps(strips)}")
        small = [f_ for f_ in merged if f_["status"] == "existing_drawn"]
        qa = {
            "drawn_band_calibration": band_cal,
            "drawn_band_vs_survey0_strip": strips,
            "small_roads": {
                "km_with_width": round(
                    sum(f_["geom"].length for f_ in small) / 1000, 1
                ),
                "km_left_out_low": small_low_km,
                "km_by_width": {
                    f"{lo}-{hi}": round(
                        sum(f_["geom"].length for f_ in small if lo <= f_["row_m"] < hi)
                        / 1000,
                        1,
                    )
                    for lo, hi in (
                        (3, 6),
                        (6, 9),
                        (9, 12),
                        (12, 18),
                        (18, 24),
                        (24, 31),
                    )
                },
            },
            "label_vs_drawn": label_check,
            "ring_roads": ring_check,
            "zr_ring_table": zr,
            "osm_check": osm,
            "km_total": km(merged),
            "km_by_row_m": {str(k): round(v, 1) for k, v in sorted(by_row.items())},
            "km_by_status": {k: round(v, 1) for k, v in by_status.items()},
            "km_by_confidence": {k: round(v, 1) for k, v in by_conf.items()},
            "labels_in_cells": len(labels),
            "worker_peak_mb": round(max(peaks) / 1e6) if peaks else None,
        }
        log(json.dumps(qa, indent=1)[:4000])
        if args.spotcheck:
            os.makedirs(args.geojson or area, exist_ok=True)
            out = os.path.join(args.geojson or area, "roads_spotcheck.png")
            spotcheck(doc, results, fit, args.spotcheck, out)
            log(f"  spotcheck -> {out}")
        if args.geojson:
            os.makedirs(args.geojson, exist_ok=True)
            n = write_geojson(
                os.path.join(args.geojson, f"{PLAN_ID}.roads.geojson"),
                merged,
                rings,
                labels,
            )
            log(f"  geojson: {n} features")
            if args.publish and not args.pages:
                published = publish(
                    os.path.join(args.geojson, f"{PLAN_ID}.roads.geojson"), n, src
                )
        doc.close()
        temp_end = qf.dir_bytes()
    if args.pages:
        log("test run (--pages): index not written")
        return
    with open(INDEX, encoding="utf-8") as f:
        ix = json.load(f)
    ix.setdefault("roads", {})[PLAN_ID] = {
        "doc_id": DOC_ID,
        "source_url": src["source_url"],
        "sha256": src["sha256"],
        "status": src["status"],
        "status_label": src["status_label"],
        "georef": fit,
        "sheet_georef_qa": sheets_qa,
        "extraction": {
            "method": "road_network_sheet",
            "script": "infra/scripts/planning/roads_mob.py",
            "dpi": 300,
            "red_edge": "#a80000",
            "row_edges_spacing_m": [5.0, 36.0],
            "label_propagation_m": 600.0,
            "deflection_deg": 30.0,
            "label_tolerance": "max(3 m, 15 %)",
            "ring_roads": "vector strokes; ROW from the sheet legend (STRR / ITRR / IRR 90 m, radial 60 m)",
        },
        "published": published,
        "sheets": sorted(per_sheet, key=lambda r: r["grid"]),
        "qa": qa,
        "built_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    }
    with open(INDEX, "w", encoding="utf-8", newline="\n") as f:
        # same layout as build_layer_index.save_index; build_id untouched (zone rows unchanged)
        json.dump(ix, f, indent=1, sort_keys=False)
        f.write("\n")
    log(
        f"done in {time.time() - t0:.0f} s; temp before cleanup {temp_end / 1e6:.0f} MB"
    )


if __name__ == "__main__":
    main()
