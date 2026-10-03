#!/usr/bin/env python3
"""Build infra/planning/layer_index.json from the source URLs, one source at a time.

    python -X faulthandler build_layer_index.py --plans BDA-RMP2031,BMRDA-HSK-MP2031,BMRDA-LPA-MAP
        [--old-data-root C:/Users/tanny/Downloads/planning]   (old calibration outputs, compared only)

For each source document: download it to %TEMP%\\qnit_planning\\build\\ (sha256 checked against
infra/planning/plan_docs.csv), re-run its calibration (OpenStreetMap fetched into memory or the
temp area, never kept), run the sheet worker for QA (zones discarded), write one index row
per sheet and per LPA outline, then delete the download. Rows already "indexed" are skipped
(resumable). The index holds text only: calibration parameters, legend, extraction settings,
QA, warnings and status; no geometry.

Calibration values are compared with the previous build's (open-decisions #40): when the
re-run agrees within 1 m (affine, at the sheet corners) or 0.5 m (RMSE / floor), the previous
value is kept so that the served layer is unchanged; otherwise the re-run value is used and
the difference is reported.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# numpy's OpenBLAS reserves a buffer per thread (~800 MB committed on 32 CPUs); nothing here
# needs threaded BLAS, so one thread keeps the 2 GB worker / 1 GB service caps honest
for _v in (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ.setdefault(_v, "1")

import numpy as np
import pyarrow as pa
import pymupdf
import qnit_fetch as qf
import shapely
import winjob
from pyproj import Transformer

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
REG = os.path.join(REPO, "infra", "planning")
INDEX = os.path.join(REG, "layer_index.json")
WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sheet_worker.py")
WORKER_CAP = 2 << 30
NULL_SHIFTS_M = [
    (1500, 0),
    (-1500, 0),
    (0, 1500),
    (0, -1500),
    (2500, 2500),
    (-2500, 2500),
    (2500, -2500),
    (-3000, -1000),
]
TO_WGS = Transformer.from_crs(32643, 4326, always_xy=True)
TO_UTM = Transformer.from_crs(4326, 32643, always_xy=True)
REPORT: dict = {"steps": [], "workers": [], "kept_previous": [], "changed": []}
OSM_PENDING = "pending: Overpass unavailable 2 Oct 2026 (open-decisions #43); re-run with --osm-only"


# ---------------------------------------------------------------- helpers
def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def read_csv(name: str) -> list[dict]:
    with open(os.path.join(REG, name), encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_index() -> dict:
    if os.path.exists(INDEX):
        with open(INDEX, encoding="utf-8") as f:
            return json.load(f)
    return {"format": 1, "plans": {}, "rows": []}


def save_index(ix: dict) -> None:
    ix["rows"].sort(
        key=lambda r: (r["plan_id"], r["kind"] != "lpa_outline", r["row_id"])
    )
    body = json.dumps({k: v for k, v in ix.items() if k != "build_id"}, sort_keys=True)
    ix["build_id"] = (
        f"idx-{dt.datetime.now(dt.UTC).date().isoformat()}-{hashlib.sha1(body.encode()).hexdigest()[:7]}"
    )
    ix["built_at"] = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    tmp = INDEX + ".part"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(ix, f, indent=1, sort_keys=False)
        f.write("\n")
    os.replace(tmp, INDEX)


def upsert(ix: dict, row: dict) -> None:
    ix["rows"] = [r for r in ix["rows"] if r["row_id"] != row["row_id"]] + [row]
    save_index(ix)


def done(ix: dict, row_id: str) -> bool:
    return any(r["row_id"] == row_id and r["status"] == "indexed" for r in ix["rows"])


def frames(b: bytes):
    i = 0
    while i < len(b):
        tag = b[i : i + 4]
        n = struct.unpack("<Q", b[i + 4 : i + 12])[0]
        yield tag, b[i + 12 : i + 12 + n]
        i += 12 + n


def worker(job: dict, label: str) -> dict:
    """Run sheet_worker under the 2 GB cap; returns {meta, zones (geoms, cols), outlines}."""
    t0 = time.time()
    rc, out, err, peak = winjob.run_capped(
        [sys.executable, "-X", "faulthandler", WORKER],
        json.dumps(job).encode(),
        WORKER_CAP,
    )
    REPORT["workers"].append(
        {
            "job": label,
            "rc": rc,
            "peak_mb": round(peak / 1e6),
            "seconds": round(time.time() - t0, 1),
        }
    )
    if rc != 0:
        raise RuntimeError(
            f"worker {label} exit {rc}: {err.decode(errors='replace')[-2000:]}"
        )
    res = {"meta": {}, "zones": [], "overlays": [], "outlines": []}
    for tag, p in frames(out):
        if tag == b"META":
            res["meta"].update(json.loads(p))
        elif tag in (b"ZONE", b"OVLY"):
            t = pa.ipc.open_stream(p).read_all()
            res["zones" if tag == b"ZONE" else "overlays"].append(t)
        elif tag == b"OUTL":
            res["outlines"].append(shapely.from_wkb(p))
    log(f"  worker {label}: {round(peak / 1e6)} MB peak, {time.time() - t0:.0f}s")
    return res


def table_geoms(tables: list[pa.Table]) -> tuple[np.ndarray, dict]:
    if not tables:
        return np.array([], dtype=object), {}
    t = pa.concat_tables(tables)
    g = shapely.from_wkb(t.column("geometry").to_numpy(zero_copy_only=False))
    return g, {c: t.column(c).to_pylist() for c in t.column_names if c != "geometry"}


def extent(bounds) -> dict:
    x0, y0, x1, y1 = (float(v) for v in bounds)
    xs, ys = TO_WGS.transform([x0, x1, x0, x1], [y0, y0, y1, y1])
    return {
        "epsg32643": [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)],
        "wgs84": [
            round(min(xs), 6),
            round(min(ys), 6),
            round(max(xs), 6),
            round(max(ys), 6),
        ],
    }


def doc_row(doc_id: str) -> dict:
    return next(r for r in read_csv("plan_docs.csv") if r["doc_id"] == doc_id)


def plan_row(plan_id: str) -> dict:
    return next(r for r in read_csv("plans.csv") if r["plan_id"] == plan_id)


def fetch(doc_id: str, area: str, name: str | None = None) -> tuple[str, dict]:
    d = doc_row(doc_id)
    log(f"download {doc_id} <- {d['source_url'][:100]}")
    path, _sha = qf.download(
        d["source_url"], area, name or f"{doc_id}.pdf", d["sha256"]
    )
    REPORT["steps"].append(
        {"download": doc_id, "bytes": os.path.getsize(path), "sha256_ok": True}
    )
    return path, d


def junction_counts(sj, sdeg, oj, odeg, otree, radius=10.0, use_deg=True):
    if not len(sj):
        return 0
    idx = otree.query(shapely.points(sj), predicate="dwithin", distance=radius)
    hit = set()
    for a, b in zip(*idx, strict=True):
        if use_deg and sdeg[a] != odeg[b]:
            continue
        hit.add(int(a))
    return len(hit)


def null_check(sj, sdeg, oj, odeg, otree, matched, use_deg=True) -> dict:
    null = [
        junction_counts(sj + np.array([dx, dy]), sdeg, oj, odeg, otree, use_deg=use_deg)
        for dx, dy in NULL_SHIFTS_M
    ]
    m = float(np.mean(null)) if null else 0.0
    return {
        "matches": int(matched),
        "null_mean": round(m, 2),
        "ratio": round(matched / max(m, 1.0), 2),
        "pass_3x": bool(matched >= 3 * max(m, 1.0)),
        "shifts_m": NULL_SHIFTS_M,
    }


def overpass_roads(bbox_wgs, highways: str, tile_deg=0.045) -> list[dict]:
    """All roads of the given classes in bbox (W, S, E, N), fetched in tiles into memory."""
    w0, s0, e0, n0 = bbox_wgs
    ny = max(1, math.ceil((n0 - s0) / tile_deg))
    nx = max(1, math.ceil((e0 - w0) / tile_deg))
    els, seen = [], set()
    for i in range(ny):
        for j in range(nx):
            s_ = s0 + (n0 - s0) * i / ny
            n_ = s0 + (n0 - s0) * (i + 1) / ny
            w_ = w0 + (e0 - w0) * j / nx
            e_ = w0 + (e0 - w0) * (j + 1) / nx
            q = f'way["highway"~"^({highways})$"]({s_:.5f},{w_:.5f},{n_:.5f},{e_:.5f});'
            for x in qf.overpass(q, timeout_s=180):
                if x["id"] not in seen:
                    seen.add(x["id"])
                    els.append(x)
    log(f"  OSM roads: {len(els)} ways in {ny * nx} tiles")
    return els


def osm_tiled(query: str, bbox_str: str, bbox_wsen, tile_deg: float) -> list[dict]:
    """One Overpass query (written for `bbox_str`, "S,W,N,E") run tile by tile over bbox_wsen
    (W, S, E, N); elements deduplicated by (type, id). 2 s pause between tiles."""
    w0, s0, e0, n0 = bbox_wsen
    ny = max(1, math.ceil((n0 - s0) / tile_deg))
    nx = max(1, math.ceil((e0 - w0) / tile_deg))
    els, seen = [], set()
    for i in range(ny):
        for j in range(nx):
            s_ = s0 + (n0 - s0) * i / ny
            n_ = s0 + (n0 - s0) * (i + 1) / ny
            w_ = w0 + (e0 - w0) * j / nx
            e_ = w0 + (e0 - w0) * (j + 1) / nx
            q = query.replace(bbox_str, f"{s_:.5f},{w_:.5f},{n_:.5f},{e_:.5f}")
            for x in qf.overpass(q, timeout_s=180):
                k = (x["type"], x["id"])
                if k not in seen:
                    seen.add(k)
                    els.append(x)
            time.sleep(2)
    return els


def old_json(args, *parts):
    if not args.old_data_root:
        return None
    p = os.path.join(args.old_data_root, *parts)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def keep_or_new(what: str, new: float, old: float | None, tol: float) -> float:
    if old is None:
        return new
    d = abs(new - old)
    if d <= tol:
        REPORT["kept_previous"].append(
            {"what": what, "new": new, "old": old, "diff": round(d, 3)}
        )
        return old
    REPORT["changed"].append(
        {"what": what, "new": new, "old": old, "diff": round(d, 3)}
    )
    return new


# ---------------------------------------------------------------- BDA RMP 2031
def build_bda(ix: dict, args, outlines: dict) -> None:
    import georef_plucomp as gp

    plan_id, doc_id = "BDA-RMP2031", "BDA-RMP2031-PLUCOMP"
    plan = plan_row(plan_id)
    row_id, out_id = f"{doc_id}#p1", f"{doc_id}#lpa"
    if done(ix, row_id) and done(ix, out_id):
        # already indexed: only the outline is needed again (LPA-map fit in this run)
        row = next(r for r in ix["rows"] if r["row_id"] == out_id)
        with qf.TempArea("build/bda") as area:
            path, _d = fetch(doc_id, area)
            res = worker(
                {**row, "source_path": path, "authority": "BDA"}, f"{doc_id} outline"
            )
            outlines["BDA"] = res["outlines"][0]
        log("BDA rows already indexed; outline re-extracted")
        return
    with qf.TempArea("build/bda") as area:
        raw = os.path.join(area, "raw", plan_id)
        os.makedirs(raw, exist_ok=True)
        path, d = fetch(doc_id, raw)
        page = pymupdf.open(path)[0]
        if args.no_osm:
            # Overpass unavailable (open-decisions #43): the previous OSM-derived calibration
            # is indexed for the same source bytes (sha256 checked above); OSM checks pending
            geo = old_json(args, "georef", f"{doc_id}.json")
            if geo is None:
                raise SystemExit(
                    "--no-osm needs the previous georef output (--old-data-root)"
                )
            A = np.array(geo["affine_page_to_32643"])
            rmse = geo["georef_rmse_m"]
            nc = {"pending": OSM_PENDING}
            REPORT["kept_previous"].append(
                {"what": f"{doc_id} affine + RMSE", "why": "Overpass unavailable"}
            )
        else:
            # calibration re-run: georef_plucomp in a sandbox data root. Its OSM inputs are
            # fetched first in 0.15 deg tiles (one request at a time, backoff, mirror rotation)
            # into <area>/osm, where the script reads them; the area is deleted afterwards
            os.makedirs(os.path.join(area, "osm"), exist_ok=True)
            s0, w0, n0, e0 = (float(v) for v in gp.BBOX.split(","))
            for name in ("water", "major", "sec"):
                els = osm_tiled(gp.OSM_QUERIES[name], gp.BBOX, (w0, s0, e0, n0), 0.15)
                with open(
                    os.path.join(area, "osm", f"{name}.json"), "w", encoding="utf-8"
                ) as f:
                    json.dump({"elements": els}, f)
                log(f"  OSM {name}: {len(els)} elements")
                del els
            gp.OVERPASS = qf.OVERPASS_MIRRORS
            argv = sys.argv
            sys.argv = ["georef_plucomp.py", "--data-root", area]
            try:
                gp.main()
            finally:
                sys.argv = argv
            with open(
                os.path.join(area, "georef", f"{doc_id}.json"), encoding="utf-8"
            ) as f:
                geo = json.load(f)
            old = old_json(args, "georef", f"{doc_id}.json")
            A_new = np.array(geo["affine_page_to_32643"])
            A = A_new
            corners = np.array(
                [
                    [0, 0],
                    [page.rect.width, 0],
                    [0, page.rect.height],
                    [page.rect.width, page.rect.height],
                ]
            )
            if old:
                A_old = np.array(old["affine_page_to_32643"])
                shift = float(
                    np.hypot(
                        *(
                            gp.apply_affine(A_new, corners)
                            - gp.apply_affine(A_old, corners)
                        ).T
                    ).max()
                )
                if shift <= 1.0:
                    A = A_old
                    REPORT["kept_previous"].append(
                        {
                            "what": f"{doc_id} affine",
                            "max_corner_shift_m": round(shift, 3),
                        }
                    )
                else:
                    REPORT["changed"].append(
                        {
                            "what": f"{doc_id} affine",
                            "max_corner_shift_m": round(shift, 3),
                        }
                    )
            rmse = keep_or_new(
                f"{doc_id} georef_rmse_m",
                geo["georef_rmse_m"],
                old and old.get("georef_rmse_m"),
                0.5,
            )
            # null check: PLUCOMP road junctions vs OSM junctions (major + secondary/tertiary)
            tr = Transformer.from_crs(4326, 32643, always_xy=True)
            els = gp.osm(area, "major") + gp.osm(area, "sec")
            _lines, Jo = gp.osm_lines_and_junctions(els, tr)
            Jp = gp.junctions_from_segments(gp.plucomp_roads(page), snap=0.5)
            G = gp.apply_affine(A, Jp)
            otree = shapely.STRtree(shapely.points(Jo))
            zeros = np.zeros(len(G), int)
            matched = junction_counts(
                G, zeros, Jo, np.zeros(len(Jo), int), otree, use_deg=False
            )
            nc = null_check(
                G, zeros, Jo, np.zeros(len(Jo), int), otree, matched, use_deg=False
            )
            del els, _lines
        # QA run of the extraction (zones discarded)
        georef = {
            "method": geo["georef_method"],
            "affine_page_to_32643": [list(map(float, r)) for r in A],
            "m_per_px": geo["m_per_px"],
            "rmse_m": rmse,
            "check_points": geo["check_points"]["n"],
            "check_points_dropped": geo["check_points_dropped"],
            "poly2_check_rmse_m": geo["poly2_check_rmse_m"],
        }
        res = worker(
            {
                "extraction": {"method": "plucomp_composite"},
                "source_path": path,
                "page": 1,
                "georef": georef,
                "authority": "BDA",
            },
            doc_id,
        )
        meta = res["meta"]
        lpa = res["outlines"][0]
        outlines["BDA"] = lpa
        old_qa = old_json(args, "planning", "zones", f"{plan_id}_qa.json") or {}
        cls_check = {}
        for k, v in meta["area_ha_by_class"].items():
            o = (old_qa.get("area_ha_by_class") or {}).get(k)
            cls_check[k] = {
                "ha": v,
                "previous_ha": None if o is None else round(o, 2),
                "diff_pct": None if not o else round(100 * (v - o) / o, 3),
            }
        sheet_qa = {
            "doc_id": doc_id,
            "status": plan["status"],
            "extraction": "raster_palette",
            "georef_rmse_m": rmse,
            "m_per_px": geo["m_per_px"],
            "georef_method": geo["georef_method"],
            "legend_check": "warn",
            "qa_failures": [],
            "sheet_scale": "1:57,340 (fitted; title block says 1:5,000)",
            "source_layer": "composite",
            "sheet": None,
            "warnings": [],
        }
        common = {
            "plan_id": plan_id,
            "authority": "BDA",
            "doc_id": doc_id,
            "page": 1,
            "source_url": d["source_url"],
            "sha256": d["sha256"],
        }
        upsert(
            ix,
            {
                **common,
                "row_id": row_id,
                "kind": "zones",
                "sheet": "PLUCOMP (Proposed Land Use Composite)",
                "sheet_key": "plucomp",
                "source_layer": "composite",
                "priority": {"rank": 3, "order": [0]},
                "extent": extent(lpa.bounds),
                "georef": georef,
                "legend": {
                    "source": "infra/planning/legend_map.csv",
                    "palette": "exact colour, else nearest main colour (RGB)",
                },
                "extraction": {
                    "method": "plucomp_composite",
                    "overlays": True,
                    "clipped_in_worker": True,
                    "band_rows": 2048,
                    "band_margin_px": 640,
                    "params": {
                        "HATCH_CLOSE_PX": 2,
                        "FOREST_CLOSE_PX": 30,
                        "FOREST_DILATE_PX": 2,
                        "SLIVER_PX": 2.0,
                        "STREAM_CASING_PX": 4,
                        "STREAM_REACH_PX": 6,
                    },
                },
                "merge": {"cut_grid": None, "dissolve": False},
                "qa": {
                    "rmse_m": rmse,
                    "null_check": nc,
                    "class_check": cls_check,
                    "zones": meta["zones"],
                    "overlays": meta["overlays"],
                    "previous_zones": old_qa.get("zones")
                    if isinstance(old_qa.get("zones"), int)
                    else None,
                    "lpa_area_ha": meta["lpa_area_ha"],
                    "worker_seconds": meta["seconds"],
                    "raster": meta["raster"],
                },
                "sheet_qa": sheet_qa,
                "position_uncertainty_m": round(
                    math.sqrt(rmse**2 + geo["m_per_px"] ** 2), 2
                ),
                "placement_confirmed": True,
                "warnings": [],
                "status": "indexed",
            },
        )
        upsert(
            ix,
            {
                **common,
                "row_id": out_id,
                "kind": "lpa_outline",
                "sheet": "LPA boundary (black 1.92 pt line, PLUCOMP)",
                "sheet_key": "lpa",
                "extent_kind": "plan",
                "label": "LPA of BDA (LPD BDA boundary, PLUCOMP)",
                "extent": extent(lpa.bounds),
                "georef": {
                    "method": geo["georef_method"],
                    "affine_page_to_32643": georef["affine_page_to_32643"],
                },
                "extraction": {"method": "plucomp_outline"},
                "qa": {"area_km2": round(lpa.area / 1e6, 2)},
                "status": "indexed",
            },
        )
        ix["plans"][plan_id] = {
            "outline_row": out_id,
            "uncovered": None,
            "overlay_kinds": ["ngt_buffer", "forest_symbol", "stream_centreline"],
        }
        save_index(ix)
    log(
        f"BDA indexed: {meta['zones']} zones, null check {nc.get('ratio', 'pending')}, rmse {rmse:.2f} m"
    )


# ---------------------------------------------------------------- Hoskote MP 2031
def build_hsk(ix: dict, args, outlines: dict) -> None:
    import extract_hoskote as xh

    plan_id, doc_id = "BMRDA-HSK-MP2031", "BMRDA-HSK-MP2031-MP"
    plan = plan_row(plan_id)
    out_id = f"{doc_id}#lpa"
    with qf.TempArea("build/hsk") as area:
        path, d = fetch(doc_id, area)
        doc = pymupdf.open(path)
        common = {
            "plan_id": plan_id,
            "authority": "BMRDA-HSK",
            "doc_id": doc_id,
            "source_url": d["source_url"],
            "sha256": d["sha256"],
        }
        res = worker(
            {
                "extraction": {"method": "hsk_atlas_outline"},
                "source_path": path,
                "authority": "BMRDA-HSK",
            },
            f"{doc_id} outline",
        )
        lpa = res["outlines"][0]
        outlines["BMRDA-HSK"] = lpa
        shapely.prepare(lpa)
        old_qa = old_json(args, "planning", "zones", f"{plan_id}_qa.json") or {}
        upsert(
            ix,
            {
                **common,
                "row_id": out_id,
                "kind": "lpa_outline",
                "page": xh.LPA_PAGE,
                "sheet": "Map No. 19 (LPA boundary, dashed #a80000 line, 1:90,000)",
                "sheet_key": "lpa",
                "extent_kind": "plan",
                "label": "Hoskote LPA (Map No. 19, 1:90,000)",
                "extent": extent(lpa.bounds),
                "georef": {
                    "method": "sheet_grid_labels",
                    "grid_fit": res["meta"]["grid_fit"],
                },
                "extraction": {"method": "hsk_atlas_outline"},
                "qa": {
                    "area_km2": round(lpa.area / 1e6, 2),
                    "previous_area_km2": round(old_qa.get("lpa_area_km2") or 0, 2)
                    or None,
                    "dash_segments": res["meta"]["dash_segments"],
                },
                "status": "indexed",
            },
        )
        # OSM junctions of every public road class in the LPA box (memory only)
        x0, y0, x1, y1 = lpa.bounds
        lo = TO_WGS.transform
        bb = (*lo(x0 - 500, y0 - 500), *lo(x1 + 500, y1 + 500))
        if not args.no_osm:
            els = overpass_roads(bb, xh.OSM_HIGHWAYS)
            oj, odeg = xh.osm_junctions(els, TO_UTM)
            del els
            otree = shapely.STRtree(shapely.points(oj))
            log(f"  OSM junctions (degree >= 3): {len(oj)}")
        sh = xh.sheets(doc)
        order = sorted(
            sh, key=lambda s: (s["layer"] != "detail", s["scale"], s["map_no"])
        )
        old_sheets = {m["map_no"]: m for m in old_qa.get("sheets", [])}
        metas = {}
        for k, s in enumerate(order):
            fit = xh.grid_fit(doc[s["page"] - 1])
            r = worker(
                {
                    "extraction": {"method": "hsk_atlas_sheet"},
                    "source_path": path,
                    "page": s["page"],
                    "georef": {"grid_fit": fit},
                    "want_junctions": not args.no_osm,
                },
                f"{doc_id} Map {s['map_no']}",
            )
            m = r["meta"]
            if args.no_osm:
                # previous OSM junction check of this sheet (open-decisions #43)
                chk = dict(
                    old_sheets.get(s["map_no"], {}).get("osm_junctions")
                    or {"matched": 0}
                )
                nc = {"pending": OSM_PENDING}
            else:
                sj = np.array(m["junctions"]["xy"]).reshape(-1, 2)
                sdeg = np.array(m["junctions"]["deg"], int)
                chk = xh.junction_check(sj, sdeg, oj, odeg, otree)
                nc = null_check(sj, sdeg, oj, odeg, otree, chk.get("matched", 0))
            G, _c = table_geoms(r["zones"])
            clipped = (
                float(sum(shapely.area(shapely.intersection(G, lpa)))) / 1e4
                if len(G)
                else 0.0
            )
            b = shapely.total_bounds(G) if len(G) else lpa.bounds
            del G, r
            metas[s["map_no"]] = {
                "s": s,
                "k": k,
                "fit": fit,
                "m": m,
                "chk": chk,
                "nc": nc,
                "zone_area_ha": clipped,
                "bounds": b,
            }
            o = old_sheets.get(s["map_no"], {})
            log(
                f"  Map {s['map_no']:2d} {s['layer']:6s} matched {chk.get('matched', 0)} rmse "
                f"{chk.get('rmse_m', float('nan')):.2f} (prev {o.get('osm_junctions', {}).get('rmse_m', float('nan')):.2f}) "
                f"null x{nc.get('ratio', 'pending')} area {clipped:.1f} ha (prev {o.get('zone_area_ha', float('nan')):.1f})"
            )
        # georef floor: median OSM RMSE of the well-matched detail sheets
        good = [
            v["chk"]["rmse_m"]
            for v in metas.values()
            if v["s"]["layer"] == "detail"
            and v["chk"].get("matched", 0) >= xh.MIN_CHECKS
        ]
        floor = float(np.median(good)) if good else None
        floor = keep_or_new(
            f"{plan_id} georef floor", floor, old_qa.get("georef_floor_m"), 0.5
        )
        for n, v in metas.items():
            s, m, chk, o = v["s"], v["m"], v["chk"], old_sheets.get(n, {})
            few = chk.get("matched", 0) < xh.MIN_CHECKS
            fails = [chk["flag"]] if chk.get("flag") else []
            if (few or s["layer"] == "hobli") and floor is not None:
                used, basis = (
                    floor,
                    f"floor: median OSM RMSE of {len(good)} well-matched detail sheets",
                )
                if few:
                    fails = sorted(set(fails) | {"few ground checks"})
            else:
                used = keep_or_new(
                    f"{plan_id} Map {n} rmse",
                    chk["rmse_m"],
                    o.get("georef_used_m"),
                    0.5,
                )
                basis = "own OSM junction check"
            m_px = m["m_per_px"]
            unc = math.sqrt(used**2 + m_px**2)
            name = f"Map No. {n} ({s['hobli']})"
            sheet_qa = {
                "doc_id": doc_id,
                "status": plan["status"],
                "extraction": "raster_palette",
                "georef_rmse_m": used,
                "m_per_px": m_px,
                "georef_method": "sheet_grid_labels",
                "legend_check": "warn",
                "qa_failures": fails,
                "sheet_scale": f"1:{s['scale']:,}",
                "source_layer": s["layer"],
                "sheet": name,
                "warnings": [],
            }
            prev_area = o.get("zone_area_ha")
            upsert(
                ix,
                {
                    **common,
                    "row_id": f"{doc_id}#p{s['page']:02d}",
                    "kind": "zones",
                    "page": s["page"],
                    "sheet": name,
                    "sheet_key": f"map{n:02d}",
                    "source_layer": s["layer"],
                    "priority": {
                        "rank": {"detail": 0, "hobli": 1}[s["layer"]],
                        "order": [int(s["layer"] != "detail"), s["scale"], n],
                    },
                    "extent": extent(v["bounds"]),
                    "georef": {
                        "method": "sheet_grid_labels",
                        "grid_fit": v["fit"],
                        "grid_max_residual_m": max(v["fit"]["E"][2], v["fit"]["N"][2]),
                        "rmse_used_m": used,
                        "basis": basis,
                    },
                    "legend": {
                        "classes": [
                            {"label": a, "class_norm": b, "colour": c}
                            for a, b, c in xh.CLASSES
                        ],
                        "pale_alpha": xh.PALE_ALPHA,
                        "max_rgb_dist": xh.MAX_RGB_DIST,
                    },
                    "extraction": {
                        "method": "hsk_atlas_sheet",
                        "scale": s["scale"],
                        "map_no": n,
                        "hobli": s["hobli"],
                        "params": {
                            "FOREST_CLOSE_PX": xh.FOREST_CLOSE_PX,
                            "FOREST_OPEN_PX": xh.FOREST_OPEN_PX,
                            "HALO_MAX_UNCLASSIFIED": xh.HALO_MAX_UNCLASSIFIED,
                            "HALO_MAX_TRANSPORT": xh.HALO_MAX_TRANSPORT,
                        },
                    },
                    "clip": {"to": out_id, "min_area_m2": 0.5 * m_px * m_px},
                    "merge": {"cut_grid": None, "dissolve_grid": 0.01},
                    "qa": {
                        "rmse_m": chk.get("rmse_m"),
                        "matched": chk.get("matched", 0),
                        "sheet_junctions": chk.get("sheet_junctions", 0),
                        "null_check": v["nc"],
                        "class_check": {
                            "zone_area_ha": round(v["zone_area_ha"], 2),
                            "previous_ha": None
                            if prev_area is None
                            else round(prev_area, 2),
                            "diff_pct": None
                            if not prev_area
                            else round(
                                100 * (v["zone_area_ha"] - prev_area) / prev_area, 3
                            ),
                            "area_ha_by_class_unclipped": m["area_ha_by_class"],
                        },
                        "zones_unclipped": m["zones"],
                        "unknown_px_pct": round(m["unknown_px_pct"], 2),
                        "worker_seconds": m["seconds"],
                    },
                    "sheet_qa": sheet_qa,
                    "position_uncertainty_m": round(unc, 2),
                    "placement_confirmed": True,
                    "warnings": [],
                    "status": "indexed",
                },
            )
        ix["plans"][plan_id] = {
            "outline_row": out_id,
            "uncovered": {
                "zone_label_native": "Not coloured on the plan",
                "class_norm": "uncoloured",
                "note": "LPA area on no detail sheet or hobli map",
                "doc_id": doc_id,
                "min_area_m2": 1.0,
                "qa": {
                    "doc_id": doc_id,
                    "status": plan["status"],
                    "extraction": "raster_palette",
                    "georef_rmse_m": None,
                    "m_per_px": None,
                    "georef_method": None,
                    "legend_check": "warn",
                    "qa_failures": [],
                    "sheet_scale": None,
                    "source_layer": None,
                    "sheet": None,
                    "warnings": [],
                },
                "source_layer": "none",
            },
            "georef_floor_m": floor,
            "overlay_kinds": [],
        }
        save_index(ix)
        doc.close()
    log(f"Hoskote indexed: {len(order)} sheets, floor {floor:.2f} m")


# ---------------------------------------------------------------- BMRDA LPA map
def build_lpa_map(ix: dict, args, outlines: dict) -> None:
    import lpa_map_bmrda as lm

    doc_id = "STRR-LPA-MAP"
    if not {"BMRDA-HSK", "BDA"} <= set(outlines):
        raise SystemExit(
            "BMRDA-LPA-MAP needs the BDA and Hoskote outlines from the same run"
        )
    with qf.TempArea("build/lpamap") as area:
        path, d = fetch(doc_id, area)
        page = pymupdf.open(path)[0]
        polys = lm.fills(page)
        allu = shapely.union_all([f for f, _v in polys.values()])
        holes = [
            shapely.Polygon(r) for p in shapely.get_parts(allu) for r in p.interiors
        ]
        bda_map = max(holes, key=lambda h: h.area)
        hsk_map = polys["#fcd6b6"][1]
        p1, p2 = (
            np.array(hsk_map.centroid.coords[0]),
            np.array(bda_map.centroid.coords[0]),
        )
        q1, q2 = (
            np.array(outlines["BMRDA-HSK"].centroid.coords[0]),
            np.array(outlines["BDA"].centroid.coords[0]),
        )
        s = np.linalg.norm(q2 - q1) / np.linalg.norm(p2 - p1)
        A0 = [s, 0.0, 0.0, -s, *(q1 - s * np.array([p1[0], -p1[1]]))]
        A_new = lm.fit_icp(
            [(hsk_map, outlines["BMRDA-HSK"]), (bda_map, outlines["BDA"])], A0
        )
        old = old_json(args, "planning", "zones", "BMRDA-LPA-MAP_qa.json")
        A = A_new
        if old:
            A_old = np.array(old["affine_page_to_32643"])
            r = page.rect
            C = np.array([[r.x0, r.y0], [r.x1, r.y0], [r.x0, r.y1], [r.x1, r.y1]])

            def ap(Aa):
                return np.column_stack(
                    [
                        Aa[0] * C[:, 0] + Aa[1] * C[:, 1] + Aa[4],
                        Aa[2] * C[:, 0] + Aa[3] * C[:, 1] + Aa[5],
                    ]
                )

            shift = float(np.hypot(*(ap(A_new) - ap(A_old)).T).max())
            if shift <= 1.0:
                A = A_old
                REPORT["kept_previous"].append(
                    {
                        "what": "BMRDA-LPA-MAP affine",
                        "max_corner_shift_m": round(shift, 3),
                    }
                )
            else:
                REPORT["changed"].append(
                    {
                        "what": "BMRDA-LPA-MAP affine",
                        "max_corner_shift_m": round(shift, 3),
                    }
                )
        stats = {}
        for code, ours in outlines.items():
            h = next((hh for hh, (c, _n, _k) in lm.FILLS.items() if c == code), None)
            g = shapely.make_valid(lm.affine_apply(A, polys[h][1] if h else bda_map))
            stats[code] = lm.outline_stats(g, ours)
        upsert(
            ix,
            {
                "row_id": f"{doc_id}#lpas",
                "kind": "lpa_outline",
                "plan_id": "BMRDA-LPAS",
                "authority": None,
                "doc_id": doc_id,
                "page": 1,
                "source_url": d["source_url"],
                "sha256": d["sha256"],
                "sheet": "Local Planning Areas in Bengaluru Metropolitan Region (1:125,000)",
                "sheet_key": "lpas",
                "extent_kind": "bmrda_lpa_map",
                "extent": extent(shapely.make_valid(lm.affine_apply(A, allu)).bounds),
                "georef": {
                    "method": "affine_icp_on_lpa_outlines (BDA, Hoskote)",
                    "affine_page_to_32643": [float(v) for v in A],
                },
                "extraction": {"method": "bmrda_lpa_map"},
                "qa": {"fit_vs_own_outlines": stats},
                "status": "indexed",
            },
        )


# ---------------------------------------------------------------- Anekal (Map No. 39)
UNCONFIRMED = (
    "Placement not confirmed by an independent check; zones may be 100 m or more off. "
    "Verify on site."
)
# Map No. 39 legend: (class_norm, fallback label, fills); labels are read from the legend
ANK_CLASSES = [
    ("residential", "Residential", ["#ffff73"]),
    ("commercial", "Commercial", ["#0070ff"]),
    ("industrial", "Industrial", ["#c500ff"]),
    ("public_semi_public", "Public & Semi Public", ["#ff0000"]),
    ("open_space", "Parks & Open Spaces", ["#55ff00"]),
    ("transport", "Transportation", ["#9c9c9c", "#b2b2b2", "#828282"]),
    ("agriculture", "Agriculture", ["#d3ffbe"]),
    ("water", "Water Bodies", ["#97dbf2"]),
    ("forest", "Forest", ["#267300"]),
]
# callouts, symbols and text boxes; #e6e600 is drawn on the map but is not in the legend
ANK_ANNOTATION_FILLS = [
    "#ffffbe",
    "#000000",
    "#d5f7d0",
    "#005ce6",
    "#ffffff",
    "#e6e600",
]
# the plan's own land-use table (ha); water is two rows there (2,147.11 + 853.88)
ANK_TABLE_HA = {
    "residential": 11230.69,
    "commercial": 768.32,
    "industrial": 5099.95,
    "public_semi_public": 840.27,
    "open_space": 2003.78,
    "transport": 3943.22,
    "agriculture": 10891.44,
    "water": 3000.99,
    "forest": 2126.92,
}
# grid-label fit of Map No. 39 (page points -> EPSG:32643), 2 Oct 2026, same sha256
ANK_GRID_FIT = {
    "E": [15.8771577, 769760.777, 1.5, None],
    "N": [-15.8759160, 1434916.14, 1.5, None],
}
ANK_FRAME_PT = [154, 71, 2746, 2302]
ANK_CLIP_BUFFER_M = 100.0


def _hex(col) -> str:
    return "#" + "".join(f"{round(v * 255):02x}" for v in col[:3])


def legend_label(page, fills: list[str], frame_pt) -> tuple[str | None, list[str]]:
    """Text printed right of a small legend swatch of one of `fills`, outside the map's
    neatline (map-body fills of the same colour carry village names). Returns (label or
    None, every candidate seen, for the QA record)."""
    import re

    fx0, fy0, fx1, fy1 = frame_pt
    words = page.get_text("words")
    cands = []
    for dr in page.get_drawings():
        f = dr.get("fill")
        r = dr["rect"]
        if f is None or _hex(f) not in fills or r.width > 80 or r.height > 50:
            continue
        if fx0 < (r.x0 + r.x1) / 2 < fx1 and fy0 < (r.y0 + r.y1) / 2 < fy1:
            continue
        line = [
            w
            for w in words
            if r.x1 <= w[0] <= r.x1 + 250 and r.y0 - 4 <= (w[1] + w[3]) / 2 <= r.y1 + 4
        ]
        txt = " ".join(w[4] for w in sorted(line, key=lambda w: w[0])).strip()
        if txt:
            cands.append(txt)
    ok = [
        t
        for t in cands
        if re.fullmatch(r"[A-Za-z][A-Za-z &/,.()-]{2,60}", t)
        and not re.search(r"\d", t)
    ]
    return (max(ok, key=len) if ok else None), cands[:10]


def build_ank(ix: dict, args, outlines: dict) -> None:
    """Anekal 2031 from the official Map No. 39 (open-decisions #39, #44, #45): vector fills
    inside the neatline, clipped to BMRDA's pre-STRR Anekal extent + 100 m."""
    plan_id, doc_id = "BMRDA-ANK-MP2031", "BMRDA-ANK-MP2031-MAP39"
    plan = plan_row(plan_id)
    lpa_row = next((r for r in ix["rows"] if r["row_id"] == "STRR-LPA-MAP#lpas"), None)
    if lpa_row is None:
        raise SystemExit(
            "BMRDA-ANK-MP2031 needs the STRR-LPA-MAP#lpas row (BMRDA-LPA-MAP)"
        )
    out_id = "STRR-LPA-MAP#ank"
    with qf.TempArea("build/ank") as area:
        mpath, md = fetch("STRR-LPA-MAP", area)
        res = worker(
            {
                "extraction": {
                    "method": "bmrda_lpa_outline",
                    "authority": "BMRDA-ANK",
                    "extent": "pre_strr",
                },
                "georef": lpa_row["georef"],
                "source_path": mpath,
                "page": 1,
            },
            "BMRDA-ANK outline (LPA map)",
        )
        lpa = res["outlines"][0]
        ometa = res["meta"]["outlines"][0]
        upsert(
            ix,
            {
                "plan_id": plan_id,
                "authority": "BMRDA-ANK",
                "doc_id": "STRR-LPA-MAP",
                "source_url": md["source_url"],
                "sha256": md["sha256"],
                "row_id": out_id,
                "kind": "lpa_outline",
                "page": 1,
                "sheet": "Local Planning Areas in Bengaluru Metropolitan Region (1:125,000)",
                "sheet_key": "ank",
                "extent_kind": "plan",
                "label": "Anekal LPA, pre-STRR extent (BMRDA LPA map)",
                "extent": extent(lpa.bounds),
                "georef": lpa_row["georef"],
                "extraction": {
                    "method": "bmrda_lpa_outline",
                    "authority": "BMRDA-ANK",
                    "extent": "pre_strr",
                },
                "qa": {
                    "area_km2": ometa["area_km2"],
                    "legend_km2": ometa["legend_km2"],
                },
                "status": "indexed",
            },
        )
        path, d = fetch(doc_id, area)
        doc = pymupdf.open(path)
        page = doc[0]
        classes, label_warn, label_seen = [], [], {}
        for cnorm, fallback, fills in ANK_CLASSES:
            lab, seen = legend_label(page, fills, ANK_FRAME_PT)
            label_seen[cnorm] = seen
            if not lab:
                label_warn.append(cnorm)
            classes.append({"label": lab or fallback, "cnorm": cnorm, "fills": fills})
        log("  legend: " + json.dumps({c["cnorm"]: c["label"] for c in classes}))
        log("  legend candidates: " + json.dumps(label_seen)[:3000])
        del page
        doc.close()
        extraction = {
            "method": "vector_fill_sheet",
            "frame_pt": ANK_FRAME_PT,
            "classes": classes,
            "annotation_fills": ANK_ANNOTATION_FILLS,
            "min_area_m2": 1.0,
        }
        georef = {"method": "sheet_grid_labels", "grid_fit": ANK_GRID_FIT}
        res = worker(
            {
                "extraction": extraction,
                "georef": georef,
                "source_path": path,
                "page": 1,
                "chunk_m": 2000.0,
            },
            f"{doc_id} zones",
        )
        g, cols = table_geoms(res["zones"])
        n_invalid = int((~shapely.is_valid(g)).sum())
        if n_invalid:
            raise RuntimeError(f"{n_invalid} invalid zone polygons from the worker")
        m = res["meta"]
        clip = lpa.buffer(ANK_CLIP_BUFFER_M)
        shapely.prepare(clip)
        cg = shapely.intersection(g, clip)
        by_class: dict = {}
        for code, q in zip(cols.get("code", []), cg, strict=True):
            k = m["labels"][str(code)]["class_norm"]
            by_class[k] = by_class.get(k, 0.0) + q.area / 1e4
        check = {
            k: {
                "map_ha": round(by_class.get(k, 0.0), 1),
                "table_ha": t,
                "diff_pct": round(100 * (by_class.get(k, 0.0) - t) / t, 1),
            }
            for k, t in ANK_TABLE_HA.items()
        }
        dropped = float(shapely.area(g).sum() - shapely.area(cg).sum()) / 1e4
        # fills do not overlap (each keeps only what later fills leave visible), so areas add
        # up without a union (the union of ~all fills failed twice in the prototype)
        foot_ha = float(shapely.area(g).sum()) / 1e4
        inter_ha = float(shapely.area(shapely.intersection(g, lpa)).sum()) / 1e4
        lpa_ha = lpa.area / 1e4
        iou = inter_ha / (foot_ha + lpa_ha - inter_ha)
        bounds = shapely.total_bounds(cg[~shapely.is_empty(cg)])
        sheet_qa = {
            "doc_id": doc_id,
            "status": plan["status"],
            "extraction": "vector_pdf",
            "georef_rmse_m": 100.0,
            "m_per_px": None,
            "georef_method": "sheet_grid_labels",
            "legend_check": "warn" if label_warn else "pass",
            "qa_failures": [],
            "sheet_scale": "1:45,000",
            "source_layer": "lpa_map",
            "sheet": "Map No. 39 (Proposed Land Use, consolidated)",
            "warnings": [UNCONFIRMED],
        }
        upsert(
            ix,
            {
                "plan_id": plan_id,
                "authority": "BMRDA-ANK",
                "doc_id": doc_id,
                "source_url": d["source_url"],
                "sha256": d["sha256"],
                "row_id": f"{doc_id}#map39",
                "kind": "zones",
                "page": 1,
                "sheet": "Map No. 39 (Proposed Land Use, consolidated)",
                "sheet_key": "map39",
                "source_layer": "lpa_map",
                "priority": {"rank": 0, "order": [0, 45000, 39]},
                "extent": extent(bounds),
                "georef": {
                    **georef,
                    "grid_max_residual_m": 1.5,
                    "basis": "grid-label fit on the sheet (2 Oct 2026, same sha256); "
                    "no OSM check yet (#43, #45)",
                },
                "legend": {
                    "classes": classes,
                    "annotation_fills": ANK_ANNOTATION_FILLS,
                },
                "extraction": extraction,
                "clip": {
                    "to": out_id,
                    "buffer_m": ANK_CLIP_BUFFER_M,
                    "min_area_m2": 1.0,
                },
                "merge": {"cut_grid": None, "dissolve_grid": 0.01},
                "qa": {
                    "class_check_vs_plan_table": check,
                    "outside_clip_dropped_ha": round(dropped, 1),
                    "lpa_map_fit": {
                        "iou_vs_bmrda_pre_strr": round(iou, 4),
                        "map_ha": round(foot_ha, 1),
                        "lpa_ha": round(lpa_ha, 1),
                    },
                    "legend_labels_not_found": label_warn,
                    "legend_label_candidates": label_seen,
                    "null_check": {"pending": OSM_PENDING},
                    "zones_unclipped": m["zones"],
                    "worker_seconds": m["seconds"],
                },
                "sheet_qa": sheet_qa,
                "position_uncertainty_m": 100.0,
                "placement_confirmed": False,
                "warnings": [UNCONFIRMED],
                "status": "indexed",
            },
        )
        ix["plans"][plan_id] = {
            "outline_row": out_id,
            "uncovered": {
                "zone_label_native": "Not coloured on the plan",
                "class_norm": "uncoloured",
                "note": "LPA area not coloured on Map No. 39 (mostly roads drawn as lines)",
                "doc_id": doc_id,
                "min_area_m2": 1.0,
                "qa": {**sheet_qa, "source_layer": None, "sheet": None},
                "source_layer": "none",
            },
            "overlay_kinds": [],
        }
        save_index(ix)
        REPORT["steps"].append(
            {
                "anekal": {
                    "class_check": check,
                    "iou": round(iou, 4),
                    "dropped_ha": dropped,
                }
            }
        )
    log(f"Anekal indexed: Map No. 39, IoU {iou:.3f} vs BMRDA pre-STRR outline")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--plans", required=True)
    ap.add_argument("--old-data-root", default=None)
    ap.add_argument(
        "--no-osm", action="store_true", help="index without OSM checks (#43)"
    )
    args = ap.parse_args()
    job_dir = os.getenv(
        "QNIT_JOB_DIR"
    )  # detached run: its log folder, locked by this PID
    if job_dir:
        with open(os.path.join(job_dir, ".lock"), "w") as f:
            f.write(str(os.getpid()))
    qf.install_urlopen_counter()
    ix = load_index()
    outlines: dict = {}
    t0 = time.time()
    failed = None
    with qf.PeakMeter() as pm:
        for p in args.plans.split(","):
            try:
                {
                    "BDA-RMP2031": build_bda,
                    "BMRDA-HSK-MP2031": build_hsk,
                    "BMRDA-LPA-MAP": build_lpa_map,
                    "BMRDA-ANK-MP2031": build_ank,
                }[p](ix, args, outlines)
            except Exception:  # noqa: BLE001 - reported, then the temp area is cleared
                import traceback

                traceback.print_exc()
                failed = p
                break
    # the traceback (and any PDF it held open) is gone now: clear the build area
    import gc
    import shutil

    gc.collect()
    shutil.rmtree(os.path.join(qf.TEMP_ROOT, "build"), ignore_errors=True)
    REPORT.update(
        {
            "seconds": round(time.time() - t0),
            "peak_temp_mb": round(pm.peak / 1e6, 1),
            "temp_left_bytes": qf.dir_bytes(os.path.join(qf.TEMP_ROOT, "build")),
            "fetch": qf.stats_mb(),
            "build_id": ix.get("build_id"),
            "builder_peak_mb": round(winjob.process_peak_bytes()[1] / 1e6),
        }
    )
    print("REPORT " + json.dumps(REPORT), flush=True)
    if failed:
        raise SystemExit(
            f"failed at {failed}; rows indexed so far are kept (resumable)"
        )


if __name__ == "__main__":
    main()
