#!/usr/bin/env python3
"""Extract Hoskote Master Plan 2031 proposed zones from the map atlas into GeoParquet.

Usage:
    python extract_hoskote.py --data-root <dir> [--force] [--merge-only]

Source: BMRDA-HSK-MP2031-MP (hoskote.tpa.gov.in "Master_Plans.pdf", 87 pages). Zones are
150 dpi JPEG strips (some sheets also carry 300 dpi strips) under vector annotation; each
sheet prints a UTM 43N grid (eastings / northings every ~1 km) in its margin.

Two layers:
  detail  "Proposed Landuse - <hobli> Planning District" sheets, Map No. 23-77,
          1:10,000 and 1:5,000;
  hobli   "Proposed Landuse Analysis - <hobli>" maps, 1:15,000-1:35,000, for the rest of
          the LPA (coarser; their own position uncertainty).
Detail sheets win where both exist (1:5,000 first, then 1:10,000, then the hobli maps,
largest scale first). LPA area covered by neither is "Not coloured on the plan".

Per sheet (resumable: each finished sheet is written to
<data-root>/planning/zones/hsk_sheets/map_<no>.parquet + .json and skipped on restart):
  1. Georeference from the sheet's own grid labels (least squares, north-up).
  2. Decode each strip (stored bottom-up) and classify it straight away (memory: one
     strip of RGB at a time) by the nearest palette colour: the 10 legend classes, the
     same at 20 % over white (the sheet fades areas outside its own district), white, or
     unknown. Unknown pixels (boundary dots, PD lines, JPEG edges) are filled from
     neighbouring pixels; faded and white pixels are not this sheet's zones.
  3. Polygonise, transform to EPSG:32643, clip to the LPA.
  4. OSM check: road junctions found on the sheet (skeleton of the TRANSPORTATION class)
     vs junctions of the OSM road network (all public road classes), matched only within
     10 m and with the same degree. No line-to-nearest-road matching, no refit.
The LPA boundary is the dashed #a80000 line on Map No. 19 (1:90,000).

Writes <data-root>/planning/zones/BMRDA-HSK-MP2031.parquet, _lpa.parquet, _qa.json.
"""

import argparse
import csv
import json
import math
import os
import re
import sys
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pymupdf
import shapely
from pyproj import CRS, Transformer

sys.path.insert(0, os.path.dirname(__file__))
from extract_plucomp import (
    close,
    dilate,
    fill_from_neighbours,
    geoparquet,
    polygonise,
    skeleton_tiled,
)
from georef_plucomp import OVERPASS

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
PLANS_CSV = os.path.join(REPO, "infra", "planning", "plans.csv")
PLAN_ID = "BMRDA-HSK-MP2031"
DOC_ID = f"{PLAN_ID}-MP"
LPA_PAGE = 22  # Map No. 19, 1:90,000
LPA_STYLE = ("#a80000", 1.92)  # on the map; the legend sample is 3.12 pt
JUNCTION_CELL_M = 3.0  # transport mask resampled to ~3 m cells before skeletonising
OSM_HIGHWAYS = (
    "motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|"
    "motorway_link|trunk_link|primary_link|secondary_link|tertiary_link"
)
MAX_RGB_DIST = 45.0
PALE_ALPHA = 0.2
UNKNOWN, WHITE, PALE = 0, 1, 2
JUNCTION_MATCH_M = 10.0
MIN_CHECKS = 3  # fewer matched junctions: 'few ground checks', floor applies
FOREST_CLOSE_PX = 20  # tree icons sit ~35 px apart at 150 dpi; closing bridges the gaps (tuned on Map 57)
FOREST_OPEN_PX = (
    4  # drops thin green linework (narrower than ~8 px) from the forest mask
)
# anti-aliased edges of black symbols on white come out light grey (unclassified) or mid grey
# (transport): isolated pixels (<= N of the class in their 7x7 window) are halos, not zones
HALO_MAX_UNCLASSIFIED = 12
HALO_MAX_TRANSPORT = 6  # below a 1 px line through the window (7), so thin roads stay
JUNCTION_SNAP_PT = 0.5
GEOREF_PASS_M = 10.0
CRS_M = CRS.from_epsg(32643)
# legend label as printed (INDEX table), class_norm, colour in the JPEG raster
CLASSES = [
    ("RESIDENTIAL", "residential", "#fcfc00"),
    ("COMMERCIAL", "commercial", "#0070fc"),
    ("INDUSTRIAL", "industrial", "#a800e4"),
    ("PUBLIC & SEMI PUBLIC", "public_semi_public", "#fc0000"),
    ("PARK & OPEN SPACE", "open_space", "#54fc00"),
    ("PUBLIC UTILITY", "public_utility", "#fcaa00"),
    ("TRANSPORTATION", "transport", "#9c9c9c"),
    ("UNCLASSIFIED", "unclassified", "#e1e1e1"),
    ("AGRICULTURE", "agriculture", "#d4fcc0"),
    ("WATER BODY", "water", "#98dcf0"),
    # FOREST: the legend swatch is green tree icons on white, not a fill. The icons'
    # raster colour (median of tree pixels, Map 57) is the key; the forest area is the
    # white between the icons (see forest_regions).
    ("FOREST", "forest", "#57a634"),
]
UNCOLOURED_LABEL = "Not coloured on the plan"
# Master Plan report, Tables 66 (inside the conurbation) and 67 (outside), excluding STRR
# and Nandagudi Township (ha)
TABLE_66 = {
    "residential": 3791.46,
    "commercial": 452.52,
    "industrial": 2670.90,
    "public_semi_public": 291.43,
    "open_space": 921.84,
    "public_utility": 67.87,
    "transport": 1388.12,
    "forest": 15.99,
    "unclassified": 245.09,
    "water": 451.19,
}
TABLE_67 = {
    "residential": 637.55,
    "commercial": 20.72,
    "industrial": 13.37,
    "public_semi_public": 0.97,
    "open_space": 50.70,
    "transport": 1340.90,
    "agriculture": 21350.00,
    "forest": 2862.73,
    "water": 3348.00,
}


def hexrgb(h):
    return np.array([int(h[i : i + 2], 16) for i in (1, 3, 5)], float)


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def read_csv(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def hexcol(c):
    return "#" + "".join(f"{round(v * 255):02x}" for v in c[:3])


def grid_fit(page):
    E, N = [], []
    for w in page.get_text("words"):
        if re.fullmatch(r"\d{6}", w[4]) and 700000 < int(w[4]) < 900000:
            E.append(((w[0] + w[2]) / 2, int(w[4])))
        if re.fullmatch(r"\d{7}", w[4]) and 1400000 < int(w[4]) < 1500000:
            N.append(((w[1] + w[3]) / 2, int(w[4])))
    out = {}
    for name, pts in (("E", E), ("N", N)):
        x = np.array([a for a, _ in pts])
        v = np.array([b for _, b in pts], float)
        A = np.column_stack([x, np.ones_like(x)])
        coef, *_ = np.linalg.lstsq(A, v, rcond=None)
        out[name] = (
            float(coef[0]),
            float(coef[1]),
            float(np.abs(v - A @ coef).max()),
            len(pts),
        )
    return out


def to_ground(fit):
    aE, bE = fit["E"][:2]
    aN, bN = fit["N"][:2]
    return lambda xy: np.column_stack([aE * xy[:, 0] + bE, aN * xy[:, 1] + bN])


def sheets(doc):
    """Detail sheets and hobli maps that carry zone strips."""
    out = []
    for i, page in enumerate(doc):
        t = " ".join(page.get_text().split())
        m = re.search(r"Map No\s*:\s*(\d+)", t)
        sc = re.findall(r"1\s*:\s*([\d,]{3,})", t)
        scale = int(sc[0].replace(",", "")) if sc else None
        has_strips = any(
            x["bbox"][2] - x["bbox"][0] > 1500 for x in page.get_image_info()
        )
        if not (m and scale and has_strips):
            continue
        d = re.search(r"Proposed Landuse - (\w+) Hobli Planning District", t)
        h = re.search(r"Proposed Landuse Analysis\s*-\s*(\w+) Hobli", t)
        if d and scale in (5000, 10000):
            out.append(
                {
                    "page": i + 1,
                    "map_no": int(m.group(1)),
                    "hobli": d.group(1),
                    "scale": scale,
                    "layer": "detail",
                }
            )
        elif h and 15000 <= scale <= 35000:
            out.append(
                {
                    "page": i + 1,
                    "map_no": int(m.group(1)),
                    "hobli": h.group(1),
                    "scale": scale,
                    "layer": "hobli",
                }
            )
    return out


def palette():
    full = np.array([hexrgb(c) for _, _, c in CLASSES])
    tinted = [
        i for i, c in enumerate(CLASSES) if c[1] != "forest"
    ]  # icons have no tint
    pale = 255 - PALE_ALPHA * (255 - full[tinted])
    keys = np.vstack([full, pale, [[255, 255, 255]], [[0, 0, 0]]]).astype(np.float32)
    codes = np.concatenate(
        [
            np.arange(10, 10 + len(CLASSES)),
            np.full(len(tinted), PALE),
            [WHITE, UNKNOWN],
        ]
    )
    return keys, codes.astype(np.uint8)


def code_of(cnorm):
    return 10 + [c[1] for c in CLASSES].index(cnorm)


def boxsum(m, r):
    """Count of True in the (2r+1)^2 window around each pixel."""
    c = np.pad(m.astype(np.int32), ((r + 1, r), (r + 1, r))).cumsum(0).cumsum(1)
    return (
        c[2 * r + 1 :, 2 * r + 1 :]
        - c[: -2 * r - 1, 2 * r + 1 :]
        - c[2 * r + 1 :, : -2 * r - 1]
        + c[: -2 * r - 1, : -2 * r - 1]
    )


def forest_regions(cls):
    """Forest is drawn as tree icons on white. Close the icon mask over the gaps between
    icons, open it to drop thin green linework, and take the white / unknown / icon pixels
    inside as forest. Returns the number of forest pixels set."""
    f = code_of("forest")
    tree = cls == f
    if not tree.any():
        return 0
    region = close(tree, FOREST_CLOSE_PX)
    region = dilate(~dilate(~region, FOREST_OPEN_PX), FOREST_OPEN_PX)  # opening
    hit = region & ((cls == WHITE) | (cls == UNKNOWN) | (cls == PALE) | tree)
    cls[tree & ~region] = UNKNOWN  # stray icons / green lines: filled from neighbours
    cls[hit] = f
    return int(hit.sum())


def drop_halos(cls):
    """Isolated grey pixels around black symbols -> UNKNOWN (filled from neighbours)."""
    n = 0
    for cnorm, mx in (
        ("unclassified", HALO_MAX_UNCLASSIFIED),
        ("transport", HALO_MAX_TRANSPORT),
    ):
        m = cls == code_of(cnorm)
        iso = m & (boxsum(m, 3) <= mx)
        cls[iso] = UNKNOWN
        n += int(iso.sum())
    return n


def classify_rgb(a, keys, codes):
    flat = a.reshape(-1, 3).astype(np.float32)
    d2 = ((flat[:, None, :] - keys[None]) ** 2).sum(-1)
    j = d2.argmin(1)
    ok = np.sqrt(d2[np.arange(len(j)), j]) <= MAX_RGB_DIST
    return np.where(ok, codes[j], UNKNOWN).astype(np.uint8).reshape(a.shape[:2])


def class_raster(page, doc):
    """Class codes on the finest strip grid; strips decoded and classified one at a time."""
    strips = sorted(
        [
            x
            for x in page.get_image_info(xrefs=True)
            if x["bbox"][2] - x["bbox"][0] > 1500
        ],
        key=lambda x: x["bbox"][1],
    )
    x0, y0 = strips[0]["bbox"][0], strips[0]["bbox"][1]
    x1, y1 = strips[0]["bbox"][2], strips[-1]["bbox"][3]
    W = min(s["width"] for s in strips)  # 150 dpi grid (300 dpi strips capped)
    cpt = (x1 - x0) / W
    rpt = max((s["bbox"][3] - s["bbox"][1]) / s["height"] for s in strips)
    H = round((y1 - y0) / rpt)
    cls = np.full((H, W), WHITE, np.uint8)
    keys, codes = palette()
    for s in strips:
        pix = pymupdf.Pixmap(doc, s["xref"])
        if pix.n - pix.alpha != 3:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[
            ..., :3
        ]
        if s["transform"][3] < 0:
            a = a[::-1]
        c = classify_rgb(a, keys, codes)
        r0 = round((s["bbox"][1] - y0) / rpt)
        h = min(round((s["bbox"][3] - s["bbox"][1]) / rpt), H - r0)
        yi = np.minimum((np.arange(h) * c.shape[0] / h).astype(int), c.shape[0] - 1)
        xi = np.minimum((np.arange(W) * c.shape[1] / W).astype(int), c.shape[1] - 1)
        cls[r0 : r0 + h] = c[yi][:, xi]
        del pix, a, c
    return cls, (x0, y0, cpt, rpt)


def lpa_polygon(doc):
    """The LPA is drawn as a dashed line (separate segments) in several closed loops (main
    area plus detached enclaves). Segments are chained by nearest endpoint (gap <= 25 pt)
    and every loop that closes becomes a part of the LPA."""
    page = doc[LPA_PAGE - 1]
    segs = []
    for x in page.get_drawings():
        c = x.get("color")
        if c is None or x["rect"].x1 > 1880:
            continue
        if (hexcol(c), round(x.get("width") or 0, 2)) != LPA_STYLE:
            continue
        for it in x["items"]:
            if it[0] == "l":
                segs.append(np.array([[it[1].x, it[1].y], [it[2].x, it[2].y]]))
    used = np.zeros(len(segs), bool)
    ends = np.array(segs)
    loops = []
    while not used.all():
        i = int(np.flatnonzero(~used)[0])
        used[i] = True
        chain = [segs[i][0], segs[i][1]]
        while True:
            cand = np.flatnonzero(~used)
            if not len(cand):
                break
            cur = chain[-1]
            da = np.hypot(*(ends[cand, 0] - cur).T)
            db = np.hypot(*(ends[cand, 1] - cur).T)
            dd = np.minimum(da, db)
            k = int(dd.argmin())
            if dd[k] > 25:
                break
            j = cand[k]
            used[j] = True
            a, b = segs[j]
            chain += [a, b] if da[k] <= db[k] else [b, a]
        c = np.array(chain)
        if len(c) >= 4 and np.hypot(*(c[0] - c[-1])) <= 25:
            loops.append(shapely.make_valid(shapely.Polygon(c)))
    fit = grid_fit(page)
    return shapely.transform(shapely.union_all(loops), to_ground(fit)), fit, len(segs)


def raster_junctions(transport, m_px, px_to_ground):
    """Road junctions from the sheet itself: the TRANSPORTATION class (roads are drawn as
    grey bands in the raster) resampled to ~3 m cells, closed, skeletonised; nodes where
    >= 3 skeleton branches meet, degree = branches crossing a ring of 4 cells around it."""
    f = max(1, round(JUNCTION_CELL_M / m_px))
    H, W = (transport.shape[0] // f) * f, (transport.shape[1] // f) * f
    small = transport[:H, :W].reshape(H // f, f, W // f, f).any(axis=(1, 3))
    sk = skeleton_tiled(close(small, 1)).astype(bool)
    P = np.pad(sk, 1)
    nb = sum(
        P[1 + dy : P.shape[0] - 1 + dy, 1 + dx : P.shape[1] - 1 + dx].astype(np.uint8)
        for dy in (-1, 0, 1)
        for dx in (-1, 0, 1)
        if (dy, dx) != (0, 0)
    )
    ys, xs = np.nonzero(sk & (nb >= 3))
    centres = []
    for y, x in zip(ys.tolist(), xs.tolist(), strict=True):
        if not any(abs(y - cy) <= 3 and abs(x - cx) <= 3 for cy, cx in centres[-50:]):
            centres.append((y, x))
    R = 4
    ring = [(-R, d) for d in range(-R, R)] + [(d, R) for d in range(-R, R)]
    ring += [(R, -d) for d in range(-R, R)] + [(-d, -R) for d in range(-R, R)]
    pts, deg = [], []
    for cy, cx in centres:
        v = [
            bool(sk[cy + dy, cx + dx])
            if 0 <= cy + dy < sk.shape[0] and 0 <= cx + dx < sk.shape[1]
            else False
            for dy, dx in ring
        ]
        runs = sum(1 for k in range(len(v)) if v[k] and not v[k - 1])
        if runs >= 3:
            pts.append(((cx + 0.5) * f, (cy + 0.5) * f))
            deg.append(runs)
    if not pts:
        return np.zeros((0, 2)), np.zeros(0, int)
    return px_to_ground(np.array(pts)), np.array(deg)


def osm_roads(data_root, bbox_wgs):
    """All public roads in the LPA's box (cached): degrees need the full network."""
    import urllib.parse
    import urllib.request

    path = os.path.join(data_root, "osm", "hsk_roads.json")
    if not os.path.exists(path):
        w, s_, e, n = bbox_wgs
        q = f'way["highway"~"^({OSM_HIGHWAYS})$"]({s_:.5f},{w:.5f},{n:.5f},{e:.5f});'
        body = urllib.parse.urlencode(
            {"data": f"[out:json][timeout:240];{q}out geom;"}
        ).encode()
        for ep in OVERPASS:
            try:
                req = urllib.request.Request(
                    ep, data=body, headers={"User-Agent": "builder-prod-planning/1.0"}
                )
                with urllib.request.urlopen(req, timeout=300) as r:
                    raw = r.read()
                json.loads(raw)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(raw)
                break
            except Exception as ex:  # noqa: BLE001 - next mirror
                print(f"  overpass {ep} failed: {ex}", flush=True)
        else:
            sys.exit("error: could not fetch OSM roads for the Hoskote LPA")
    return read_json(path)["elements"]


def osm_junctions(elements, tr):
    """OSM nodes with >= 3 distinct neighbouring nodes over all road ways (= degree)."""
    nbr, xy = {}, {}
    for e in elements:
        if e["type"] != "way" or len(e.get("geometry", [])) < 2:
            continue
        ids = e.get("nodes", [])
        for i, nid in enumerate(ids):
            q = e["geometry"][i]
            xy.setdefault(nid, tr.transform(q["lon"], q["lat"]))
            for j in (i - 1, i + 1):
                if 0 <= j < len(ids):
                    nbr.setdefault(nid, set()).add(ids[j])
    J = np.array([(xy[k][0], xy[k][1], len(v)) for k, v in nbr.items() if len(v) >= 3])
    return J[:, :2], J[:, 2].astype(int)


def junction_check(sj, sdeg, oj, odeg, otree):
    if not len(sj):
        return {"sheet_junctions": 0, "matched": 0}
    idx = otree.query(
        shapely.points(sj), predicate="dwithin", distance=JUNCTION_MATCH_M
    )
    best = {}
    for a, b in zip(*idx, strict=True):
        if sdeg[a] != odeg[b]:
            continue
        d = float(np.hypot(*(sj[a] - oj[b])))
        if a not in best or d < best[a][0]:
            best[a] = (d, oj[b] - sj[a])
    if not best:
        return {"sheet_junctions": len(sj), "matched": 0}
    d = np.array([v[0] for v in best.values()])
    v = np.array([v[1] for v in best.values()])
    rmse = float(np.sqrt(np.mean(d**2)))
    return {
        "sheet_junctions": len(sj),
        "matched": len(d),
        "rmse_m": rmse,
        "median_m": float(np.median(d)),
        "mean_de_m": float(v[:, 0].mean()),
        "mean_dn_m": float(v[:, 1].mean()),
        "flag": "" if rmse <= GEOREF_PASS_M else f"RMSE > {GEOREF_PASS_M:.0f} m",
    }


def process_sheet(doc, s, lpa, plan, osm_j, out_dir):
    page = doc[s["page"] - 1]
    fit = grid_fit(page)
    cls, (x0, y0, cpt, rpt) = class_raster(page, doc)
    forest_px = forest_regions(cls)
    halo_px = drop_halos(cls)
    unknown = cls == UNKNOWN
    unknown_pct = 100 * float(unknown.mean())
    cls = fill_from_neighbours(cls, unknown)
    del unknown
    m_px = abs(fit["E"][0]) * cpt
    g = to_ground(fit)

    def px_to_ground(xy):
        return g(np.column_stack([x0 + xy[:, 0] * cpt, y0 + xy[:, 1] * rpt]))

    transport_code = 10 + [c[1] for c in CLASSES].index("transport")
    sj, sdeg = raster_junctions(cls == transport_code, m_px, px_to_ground)
    chk = junction_check(sj, sdeg, *osm_j)
    grid_res = max(fit["E"][2], fit["N"][2])
    # provisional; merge() applies the floor for sheets with fewer than 3 ground checks
    georef_m = chk.get("rmse_m") if chk.get("matched", 0) >= 3 else grid_res
    unc = math.sqrt(georef_m**2 + m_px**2)
    qa = {
        "doc_id": DOC_ID,
        "status": plan["status"],
        "extraction": "raster_palette",
        "georef_rmse_m": georef_m,
        "m_per_px": m_px,
        "georef_method": "sheet_grid_labels",
        "legend_check": "warn",
        "qa_failures": [] if not chk.get("flag") else [chk["flag"]],
        "sheet_scale": f"1:{s['scale']:,}",
    }

    polys = polygonise(np.where(cls >= 10, cls, 0).astype(np.uint8))
    del cls
    rows, geoms = [], []
    for code, gp in polys.items():
        if code < 10:
            continue
        for part in shapely.get_parts(
            shapely.make_valid(shapely.transform(gp, px_to_ground))
        ):
            if not lpa.contains(part):
                part = shapely.intersection(part, lpa)
            for q in shapely.get_parts(part):
                if q.geom_type != "Polygon" or q.area < 0.5 * m_px * m_px:
                    continue
                rows.append({"code": int(code)})
                geoms.append(q)
    meta = {
        **s,
        "m_per_px": m_px,
        "grid_max_residual_m": grid_res,
        "grid_labels": fit["E"][3] + fit["N"][3],
        "unknown_px_pct": unknown_pct,
        "forest_px": forest_px,
        "halo_px_dropped": halo_px,
        "position_uncertainty_m": unc,
        "osm_junctions": chk,
        "qa": qa,
        "zone_area_ha": sum(q.area for q in geoms) / 1e4,
    }
    base = os.path.join(out_dir, f"map_{s['map_no']:02d}")
    geoparquet(
        base + ".parquet.part",
        pa.table({"code": [r["code"] for r in rows]}),
        np.array(geoms, dtype=object),
        CRS_M,
    )
    with open(base + ".json", "w") as f:
        json.dump(meta, f, indent=1)
    os.replace(
        base + ".parquet.part", base + ".parquet"
    )  # parquet last: marks the sheet done
    return meta


def sheet_floor(order, out_dir):
    """Sheet metadata with the georef floor applied: sheets with fewer than MIN_CHECKS OSM
    matches, and all hobli maps, use the median RMSE of the well-matched detail sheets."""
    metas = {
        s["map_no"]: read_json(os.path.join(out_dir, f"map_{s['map_no']:02d}.json"))
        for s in order
    }
    good = [
        m["osm_junctions"]["rmse_m"]
        for m in metas.values()
        if m["layer"] == "detail" and m["osm_junctions"].get("matched", 0) >= MIN_CHECKS
    ]
    floor = float(np.median(good)) if good else None
    for m in metas.values():
        few = m["osm_junctions"].get("matched", 0) < MIN_CHECKS
        if (few or m["layer"] == "hobli") and floor is not None:
            m["georef_used_m"] = floor
            m["georef_basis"] = (
                f"floor: median OSM RMSE of {len(good)} well-matched detail sheets"
            )
            if few:
                m["qa"]["qa_failures"] = sorted(
                    set(m["qa"]["qa_failures"]) | {"few ground checks"}
                )
        else:
            m["georef_used_m"] = m["osm_junctions"]["rmse_m"]
            m["georef_basis"] = "own OSM junction check"
        m["qa"]["georef_rmse_m"] = m["georef_used_m"]
        m["position_uncertainty_m"] = math.sqrt(
            m["georef_used_m"] ** 2 + m["m_per_px"] ** 2
        )
    return metas, floor


def merge_sheet(s, meta, covs, out_dir):
    """Cut one sheet's pieces by the footprints of the higher-priority sheets they touch, then
    dissolve touching pieces of the same class (no size filter in the dissolve). Writes
    merged_NN.parquet (written last: marks the sheet merged), foot_NN.wkb and merged_NN.json."""
    n = s["map_no"]
    t = pq.read_table(os.path.join(out_dir, f"map_{n:02d}.parquet"))
    G = shapely.from_wkb(t.column("geometry").to_numpy(zero_copy_only=False))
    codes = t.column("code").to_pylist()
    del t
    # only the footprints whose box overlaps this sheet are loaded (memory)
    sb = shapely.total_bounds(G)
    feet = [
        load_foot(out_dir, k)
        for bx, k in covs
        if bx[0] <= sb[2] and bx[2] >= sb[0] and bx[1] <= sb[3] and bx[3] >= sb[1]
    ]
    for f in feet:
        shapely.prepare(f)
    ca = np.array(feet, dtype=object)
    tree = shapely.STRtree(ca) if len(ca) else None
    min_a = 0.5 * meta["m_per_px"] ** 2  # cut slivers below half a pixel (as before)
    by_code, before = {}, 0
    for code, q in zip(codes, G, strict=True):
        if tree is not None:
            idx = tree.query(q)
            idx = idx[shapely.intersects(ca[idx], q)] if len(idx) else idx
            if len(idx):
                # intersection, not clip_by_rect: clip_by_rect can return invalid
                # geometry, and union_all crashed GEOS on it (Map 57, access violation)
                bx = shapely.box(*q.bounds)
                cut = [shapely.intersection(c, bx) for c in ca[idx]]
                q = shapely.difference(q, shapely.union_all(cut))
        for p in shapely.get_parts(q):
            if p.geom_type != "Polygon" or p.area < min_a:
                continue
            by_code.setdefault(code, []).append(p)
            before += 1
    del G
    out_g, out_c = [], []
    for code in sorted(by_code):
        u = shapely.union_all(np.array(by_code[code], dtype=object), grid_size=0.01)
        for p in shapely.get_parts(u):
            if p.geom_type == "Polygon" and not p.is_empty:
                out_g.append(p)
                out_c.append(code)
    del by_code
    out_g = np.array(out_g, dtype=object)
    foot = shapely.union_all(out_g, grid_size=0.01) if len(out_g) else None
    base = os.path.join(out_dir, f"merged_{n:02d}")
    with open(os.path.join(out_dir, f"foot_{n:02d}.wkb"), "wb") as f:
        f.write(shapely.to_wkb(foot) if foot is not None else b"")
    with open(base + ".json", "w") as f:
        json.dump(
            {"pieces_before_dissolve": before, "pieces_after_dissolve": len(out_g)}, f
        )
    pq.write_table(
        pa.table(
            {"code": out_c, "geometry": pa.array(shapely.to_wkb(out_g), pa.binary())}
        ),
        base + ".parquet.part",
    )
    os.replace(base + ".parquet.part", base + ".parquet")
    return foot


def load_foot(out_dir, n):
    with open(os.path.join(out_dir, f"foot_{n:02d}.wkb"), "rb") as f:
        raw = f.read()
    return shapely.from_wkb(raw) if raw else None


def merge(args, order, metas, out_dir):
    """Priority merge, one sheet at a time. Only each footprint's box stays in memory; the
    footprints themselves are read from foot_NN.wkb when needed. Resumes: sheets with a
    merged_NN.parquet are skipped."""
    covs, t_m = [], time.time()
    for s in order:
        n = s["map_no"]
        done = os.path.join(out_dir, f"merged_{n:02d}.parquet")
        foot = (
            load_foot(out_dir, n)
            if os.path.exists(done)
            else merge_sheet(s, metas[n], covs, out_dir)
        )
        c = read_json(os.path.join(out_dir, f"merged_{n:02d}.json"))
        metas[n].update(c)
        if foot is not None:
            covs.append((tuple(shapely.total_bounds(foot)), n))
        del foot
        print(
            f"  merged Map {n} ({c['pieces_before_dissolve']} -> "
            f"{c['pieces_after_dissolve']} pieces, {time.time() - t_m:.0f}s)",
            flush=True,
        )
    return covs


QA_TYPE = pa.struct(
    [
        ("doc_id", pa.string()),
        ("status", pa.string()),
        ("extraction", pa.string()),
        ("georef_rmse_m", pa.float64()),
        ("m_per_px", pa.float64()),
        ("georef_method", pa.string()),
        ("legend_check", pa.string()),
        ("qa_failures", pa.list_(pa.string())),
        ("sheet_scale", pa.string()),
    ]
)
ZONE_SCHEMA = pa.schema(
    [
        ("zone_uid", pa.string()),
        ("plan_id", pa.string()),
        ("doc_id", pa.string()),
        ("zone_label_native", pa.string()),
        ("zone_code_native", pa.string()),
        ("class_norm", pa.string()),
        ("status", pa.string()),
        ("status_label", pa.string()),
        ("status_condition", pa.string()),
        ("inferred_under_hatch", pa.bool_()),
        ("inferred_under_stream", pa.bool_()),
        ("note", pa.string()),
        ("cartographic", pa.bool_()),
        ("area_m2", pa.float64()),
        ("source_layer", pa.string()),
        ("source_scale", pa.string()),
        ("sheet", pa.string()),
        ("position_uncertainty_m", pa.float64()),
        ("qa", QA_TYPE),
        ("geometry", pa.binary()),
    ]
)


def _polys(g):
    return [
        q for q in shapely.get_parts(g) if q.geom_type == "Polygon" and not q.is_empty
    ]


def _overlay(fn, *a):
    """Plain overlay; on a GEOS topology error, retry on the footprints' 1 cm grid."""
    try:
        return fn(*a)
    except shapely.errors.GEOSException:
        return fn(*a, grid_size=0.01)


def uncovered(lpa, covs, out_dir, tile=2000.0):
    """LPA minus every footprint, on 2 km tiles (each footprint read once, clipped to the
    tiles it overlaps), then the tile pieces are dissolved back across the tile seams.
    Polygon parts only: union_all crashed GEOS (access violation) on the mixed
    collections that intersection can return."""
    x0, y0, x1, y1 = lpa.bounds
    tiles = [
        shapely.box(x, y, x + tile, y + tile)
        for x in np.arange(x0, x1, tile)
        for y in np.arange(y0, y1, tile)
    ]
    cuts = {i: [] for i in range(len(tiles))}
    tree = shapely.STRtree(tiles)
    t_u = time.time()
    for bx, k in covs:
        foot = load_foot(out_dir, k)
        for i in tree.query(shapely.box(*bx)):
            cuts[i].extend(_polys(_overlay(shapely.intersection, foot, tiles[i])))
        del foot
    print(
        f"  footprints clipped to {len(tiles)} tiles ({time.time() - t_u:.0f}s)",
        flush=True,
    )
    pieces = []
    for i, t in enumerate(tiles):
        part = _overlay(shapely.intersection, lpa, t)
        if not part.is_empty and cuts[i]:
            cut = _overlay(shapely.union_all, np.array(cuts[i], dtype=object))
            part = _overlay(shapely.difference, part, cut)
        cuts[i] = None
        pieces.extend(_polys(part))
        if (i + 1) % 25 == 0:
            print(
                f"  uncovered: tile {i + 1}/{len(tiles)} ({time.time() - t_u:.0f}s)",
                flush=True,
            )
    if not pieces:
        return shapely.Polygon()
    return _overlay(shapely.union_all, np.array(pieces, dtype=object))


def write_zones(path, order, metas, covs, lpa, plan, out_dir):
    """Concatenate the per-sheet merged files (no global union of pieces) and add the LPA area
    outside every footprint as 'Not coloured on the plan'. Streams one sheet at a time."""
    rest = uncovered(lpa, covs, out_dir)
    rest_parts = [
        p for p in shapely.get_parts(rest) if p.geom_type == "Polygon" and p.area >= 1.0
    ]
    del rest
    boxes = [bx for bx, _k in covs]
    if rest_parts:
        boxes.append(shapely.total_bounds(np.array(rest_parts, dtype=object)))
    b = np.array(boxes)
    geo = {
        "version": "1.1.0",
        "primary_column": "geometry",
        "columns": {
            "geometry": {
                "encoding": "WKB",
                "geometry_types": ["Polygon"],
                "crs": CRS_M.to_json_dict(),
                "bbox": [
                    float(b[:, 0].min()),
                    float(b[:, 1].min()),
                    float(b[:, 2].max()),
                    float(b[:, 3].max()),
                ],
            }
        },
    }
    schema = ZONE_SCHEMA.with_metadata({b"geo": json.dumps(geo).encode()})
    none_meta = {
        "position_uncertainty_m": None,
        "qa": {
            "doc_id": DOC_ID,
            "status": plan["status"],
            "extraction": "raster_palette",
            "georef_rmse_m": None,
            "m_per_px": None,
            "georef_method": None,
            "legend_check": "warn",
            "qa_failures": [],
            "sheet_scale": None,
        },
    }
    none_sheet = {"layer": "none", "scale": None, "map_no": None, "hobli": None}
    by, layer_area, uid = {}, {}, 0

    def batch(rows, geoms):
        nonlocal uid
        for r in rows:
            uid += 1
            r["zone_uid"] = f"{PLAN_ID}-{uid:06d}"
            by[r["class_norm"]] = by.get(r["class_norm"], 0.0) + r["area_m2"]
        wkb = shapely.to_wkb(np.array(geoms, dtype=object))
        for r, w in zip(rows, wkb, strict=True):
            r["geometry"] = w
        return pa.Table.from_pylist(rows, schema=schema)

    with pq.ParquetWriter(path + ".part", schema, compression="zstd") as w:
        for s in order:
            n, meta = s["map_no"], metas[s["map_no"]]
            t = pq.read_table(os.path.join(out_dir, f"merged_{n:02d}.parquet"))
            G = shapely.from_wkb(t.column("geometry").to_numpy(zero_copy_only=False))
            rows = []
            for code, p in zip(t.column("code").to_pylist(), G, strict=True):
                label, cnorm, _ = CLASSES[code - 10]
                rows.append(row(plan, label, cnorm, p.area, s, meta))
                layer_area[s["layer"]] = layer_area.get(s["layer"], 0.0) + p.area
            if rows:
                w.write_table(batch(rows, G))
            del t, G, rows
        rows = [
            row(plan, UNCOLOURED_LABEL, "uncoloured", p.area, none_sheet, none_meta)
            for p in rest_parts
        ]
        layer_area["none"] = sum(p.area for p in rest_parts)
        if rows:
            w.write_table(batch(rows, rest_parts))
    os.replace(path + ".part", path)
    total = sum(layer_area.values())
    diff_pct = 100.0 * (total - lpa.area) / lpa.area
    area_check = {
        "zones_total_ha": round(total / 1e4, 1),
        "lpa_ha": round(lpa.area / 1e4, 1),
        "diff_pct": round(diff_pct, 3),
        "pass": abs(diff_pct) <= 0.5,
    }
    return uid, by, layer_area, area_check


def row(plan, label, cnorm, area, s, meta):
    return {
        "plan_id": PLAN_ID,
        "doc_id": DOC_ID,
        "zone_label_native": label,
        "zone_code_native": None,
        "class_norm": cnorm,
        "status": plan["status"],
        "status_label": plan["status_label"],
        "status_condition": plan.get("status_condition") or None,
        "inferred_under_hatch": False,
        "inferred_under_stream": False,
        "note": None
        if s["layer"] != "none"
        else "LPA area on no detail sheet or hobli map",
        "cartographic": False,
        "area_m2": float(area),
        "source_layer": s["layer"],
        "source_scale": f"1:{s['scale']:,}" if s.get("scale") else None,
        "sheet": f"Map No. {s['map_no']} ({s['hobli']})" if s.get("map_no") else None,
        "position_uncertainty_m": meta["position_uncertainty_m"],
        "qa": meta["qa"],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", default=os.getenv("PLANNING_DATA_ROOT"))
    ap.add_argument("--force", action="store_true", help="redo finished sheets")
    ap.add_argument("--merge-only", action="store_true")
    ap.add_argument(
        "--remerge", action="store_true", help="redo the merge from scratch"
    )
    ap.add_argument("--sheet", type=int, help=argparse.SUPPRESS)  # child: one sheet
    args = ap.parse_args()
    t0 = time.time()
    doc = pymupdf.open(os.path.join(args.data_root, "raw", PLAN_ID, f"{DOC_ID}.pdf"))
    plan = next(r for r in read_csv(PLANS_CSV) if r["plan_id"] == PLAN_ID)
    zdir = os.path.join(args.data_root, "planning", "zones")
    out_dir = os.path.join(zdir, "hsk_sheets")
    os.makedirs(out_dir, exist_ok=True)

    lpa, lpa_fit, nseg = lpa_polygon(doc)
    # polygon parts only: the chained dash loops leave a stray 10.8 m LineString, and the
    # mixed collection breaks overlays (zero area, so areas are unchanged)
    lpa = shapely.union_all(
        [p for p in shapely.get_parts(lpa) if p.geom_type == "Polygon"]
    )
    shapely.prepare(lpa)
    print(
        f"LPA polygon {lpa.area / 1e6:.1f} km2 from {nseg} dash segments (Map No. 19)",
        flush=True,
    )
    sh = sheets(doc)
    order = sorted(sh, key=lambda s: (s["layer"] != "detail", s["scale"], s["map_no"]))
    print(
        f"{sum(s['layer'] == 'detail' for s in sh)} detail sheets, {sum(s['layer'] == 'hobli' for s in sh)} hobli maps",
        flush=True,
    )

    jpath = os.path.join(out_dir, "osm_junctions.npz")
    if args.sheet is not None:  # child process: one sheet, then exit (frees memory)
        z = np.load(jpath)
        osm_j = (z["xy"], z["deg"], shapely.STRtree(shapely.points(z["xy"])))
        s = next(x for x in sh if x["map_no"] == args.sheet)
        process_sheet(doc, s, lpa, plan, osm_j, out_dir)
        return
    if not args.merge_only:
        if not os.path.exists(jpath):
            tr = Transformer.from_crs(4326, 32643, always_xy=True)
            lo = Transformer.from_crs(32643, 4326, always_xy=True).transform
            x0_, y0_, x1_, y1_ = lpa.bounds
            bb = (*lo(x0_ - 500, y0_ - 500), *lo(x1_ + 500, y1_ + 500))
            oj, odeg = osm_junctions(osm_roads(args.data_root, bb), tr)
            np.savez(jpath, xy=oj, deg=odeg)
        print(
            f"OSM road junctions (degree >= 3): {len(np.load(jpath)['deg'])}",
            flush=True,
        )
        for s in order:
            done = os.path.join(out_dir, f"map_{s['map_no']:02d}.parquet")
            if os.path.exists(done) and not args.force:
                continue
            args.remerge = True  # a sheet changed: earlier merge results are stale
            import subprocess

            r = subprocess.run(
                [
                    sys.executable,
                    __file__,
                    "--data-root",
                    args.data_root,
                    "--sheet",
                    str(s["map_no"]),
                ],
                check=False,
            )
            if r.returncode != 0:
                sys.exit(
                    f"sheet Map {s['map_no']} failed (exit {r.returncode}); finished sheets are kept"
                )
            m = read_json(os.path.join(out_dir, f"map_{s['map_no']:02d}.json"))
            o = m["osm_junctions"]
            print(
                f"  Map {s['map_no']:2d} {s['layer']:6s} {s['hobli']:15s} 1:{s['scale']:>6,} {m['m_per_px']:5.2f} m/px "
                f"grid res {m['grid_max_residual_m']:.1f} m, {m['zone_area_ha']:7.1f} ha, junctions {o.get('matched', 0)}/"
                f"{o.get('sheet_junctions', 0)} rmse {o.get('rmse_m', float('nan')):.1f} m ({time.time() - t0:.0f}s)",
                flush=True,
            )

    if args.remerge:
        for f in os.listdir(out_dir):
            if f.startswith(("merged_", "foot_")):
                os.remove(os.path.join(out_dir, f))
    metas, floor = sheet_floor(order, out_dir)
    covs = merge(args, order, metas, out_dir)
    nz, by, layer_area, area_check = write_zones(
        os.path.join(zdir, f"{PLAN_ID}.parquet"), order, metas, covs, lpa, plan, out_dir
    )
    del covs
    print(f"area check: {area_check}", flush=True)
    geoparquet(
        os.path.join(zdir, f"{PLAN_ID}_lpa.parquet"),
        pa.table(
            {
                "plan_id": [PLAN_ID],
                "authority": [plan["authority"]],
                "label": ["Hoskote LPA (Map No. 19, 1:90,000)"],
            }
        ),
        np.array([lpa], dtype=object),
        CRS_M,
    )

    sheet_meta = [metas[s["map_no"]] for s in order]
    qa = {
        "lpa_area_km2": lpa.area / 1e6,
        "lpa_source": "Map No. 19 dashed #a80000 line, 1:90,000",
        "lpa_grid_residual_m": max(lpa_fit["E"][2], lpa_fit["N"][2]),
        "zones": nz,
        "pieces_before_dissolve": sum(
            m["pieces_before_dissolve"] for m in metas.values()
        ),
        "pieces_after_dissolve": sum(
            m["pieces_after_dissolve"] for m in metas.values()
        ),
        "zone_area_ha_by_class": {k: round(v / 1e4, 2) for k, v in sorted(by.items())},
        "area_ha_by_layer": {k: round(v / 1e4, 1) for k, v in layer_area.items()},
        "area_check": area_check,
        "tables": {
            "66_inside_conurbation": TABLE_66,
            "67_outside_conurbation": TABLE_67,
        },
        "georef_floor_m": floor,
        "sheets": sheet_meta,
        "params": {
            "MAX_RGB_DIST": MAX_RGB_DIST,
            "PALE_ALPHA": PALE_ALPHA,
            "JUNCTION_MATCH_M": JUNCTION_MATCH_M,
        },
    }
    with open(os.path.join(zdir, f"{PLAN_ID}_qa.json"), "w") as f:
        json.dump(qa, f, indent=1)
    print(
        json.dumps(
            {
                k: qa[k]
                for k in (
                    "lpa_area_km2",
                    "zones",
                    "pieces_before_dissolve",
                    "pieces_after_dissolve",
                    "zone_area_ha_by_class",
                    "area_ha_by_layer",
                    "area_check",
                )
            },
            indent=1,
        )
    )
    print(f"done ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
