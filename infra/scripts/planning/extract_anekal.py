#!/usr/bin/env python3
"""Extract Anekal LPA Master Plan 2031 (final, GO UDD 151 BMR 2013, 03-09-2014) zones.

Usage:
    python extract_anekal.py --data-root <dir> --primeocr-dir <dir> [--merge-only]

Source: BMRDA-ANK-MP2031-MP, "Anekal MP.pdf" (22 raster sheets, 110 ppi), copied to
<data-root>/raw/BMRDA-ANK-MP2031/.

Why not load the primeocr merged layer as it is: its classes sum to 71,840 ha against the
plan's 40,230 ha (Map No. 39 table), because the 1:45,000 title map and the planning-district
maps overlap the 1:10,000 sheets. So the Hoskote method is used on the raw sheets, with
primeocr's per-page results as inputs:
  - georeference: primeocr `affine_px_to_crs` (UTM 43N from the margin grid labels, OCR),
    pages with georef status "ok" only (18 of 22; the 4 planning-district maps failed),
    except Map No. 60 (Circulation Pattern: a roads plan, not land use);
  - legend palette: primeocr's per-page legend colours (hex + extra shades);
  - map frame: primeocr `map_rect`.
Layers: detail = the 1:10,000 sheets (SP, AT, JI, AN ...); lpa_map = Map No. 39 (1:45,000)
for the rest of the LPA. The LPA boundary is the coloured extent of Map No. 39.

Writes <data-root>/planning/zones/BMRDA-ANK-MP2031.parquet, _lpa.parquet, _qa.json.
"""

import argparse
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

import numpy as np
import pyarrow.parquet as pq
import pymupdf
import shapely
from extract_hoskote import osm_junctions, read_csv, read_json
from pyproj import Transformer
from raster_plan import (
    LAYER_RANK,
    merge,
    osm_index,
    process_sheet,
    sheet_floor,
    write_lpa,
    write_zones,
)

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
PLANS_CSV = os.path.join(REPO, "infra", "planning", "plans.csv")
PLAN_ID = "BMRDA-ANK-MP2031"
DOC_ID = "BMRDA-ANK-MP2031-MP"
MAX_RGB_DIST = 45.0
# Map No. 39 "Proposed Landuse Analysis - Anekal LPA" (whole LPA, ha): urbanisable +
# non-urbanisable rows added per class
TABLE_LPA = {
    "residential": 11230.69,
    "commercial": 768.32,
    "industrial": 5099.95,
    "public_semi_public": 840.27,
    "open_space": 2003.78,
    "public_utility": 31.16,
    "transport": 3943.22,
    "agriculture": 630.48 + 10260.96,
    "water": 2147.11,
    "forest": 33.46 + 2093.46,
    "hillock": 43.31 + 249.98,
}
TABLE_TOTAL_HA = 40230.03
CLASS_NORM = {
    "RESIDENTIAL": "residential",
    "COMMERCIAL": "commercial",
    "INDUSTRIAL": "industrial",
    "PUBLIC & SEMI PUBLIC": "public_semi_public",
    "PARK & OPEN SPACE": "open_space",
    "PUBLIC UTILITY": "public_utility",
    "TRANSPORTATION": "transport",
    "AGRICULTURE": "agriculture",
    "WATER BODIES": "water",
    "FOREST": "forest",
    "HILLOCKS/QUARRIES": "hillock",
}
LABELS = list(CLASS_NORM)


def lab_to_hex(lab):
    L, a, b = lab
    fy = (L + 16) / 116
    fx, fz = fy + a / 500, fy - b / 200

    def f(t):
        return t**3 if t**3 > 0.008856 else (t - 16 / 116) / 7.787

    X, Y, Z = 0.95047 * f(fx), 1.0 * f(fy), 1.08883 * f(fz)
    r = 3.2406 * X - 1.5372 * Y - 0.4986 * Z
    g = -0.9689 * X + 1.8758 * Y + 0.0415 * Z
    bb = 0.0557 * X - 0.2040 * Y + 1.0570 * Z

    def gam(c):
        c = max(0.0, min(1.0, c))
        return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055

    return "#" + "".join(f"{round(255 * gam(c)):02x}" for c in (r, g, bb))


# Hatched legend classes: their hatch-line colours (#e4b850, #959899) also match edges and
# linework on every sheet (round 1 gave 2,197 ha public utility and 3,362 ha hillocks against
# 31 and 293 ha in the plan's table), so they are not extracted; their pixels are filled from
# their neighbours. docs/plans/open-decisions.md #13.
NOT_EXTRACTED = {"PUBLIC UTILITY", "HILLOCKS/QUARRIES"}
ROAD_GREYS = ["#a0a0a0", "#959899", "#8c8c8c"]
HATCH_WARNING = (
    "This sheet has hatched classes (public utility, hillocks/quarries; 324 ha across the LPA "
    "per the plan) that are not extracted. The zone shown here may be one of them."
)


def page_classes(report):
    """Class list in fixed order, colours from this page's legend (hex + extra shades). A
    class with no colour gets no palette key (it can never be assigned)."""
    pal = {p["name"]: p for p in report["palette"]}
    out = []
    for name in LABELS:
        p = pal.get(name)
        cols = []
        if p and name not in NOT_EXTRACTED:
            cols = [p["hex"]] + [lab_to_hex(e) for e in p.get("extra", [])]
        if name == "TRANSPORTATION":
            # road bands are drawn mid-grey on the map; the legend swatch is a light grey
            # (#ddd8d5), so mid-grey went to the hillock hatch key (round 1) and then to
            # forest (round 2, seen on the acceptance pages)
            cols += ROAD_GREYS
        out.append({"label": name, "cnorm": CLASS_NORM[name], "colours": cols})
    return out


def sheets(doc_path, pdir):
    out = []
    for f in sorted(glob.glob(os.path.join(pdir, "Anekal_MP_p*_report.json"))):
        r = read_json(f)
        md = r.get("metadata", {})
        ok = (r.get("georef") or {}).get("status") == "ok"
        scale = md.get("scale")
        if not ok:
            continue
        if "circulation" in (md.get("title") or "").lower():
            continue  # Map No. 60 Circulation Pattern: roads plan, not land use
        layer = "detail" if scale and scale <= 10000 else "lpa_map"
        page = r["page"]
        W, H = r["image_size"]

        def load(page=page, W=W, H=H):
            d = pymupdf.open(doc_path)
            pg = d[page - 1]
            pix = pg.get_pixmap(
                matrix=pymupdf.Matrix(W / pg.rect.width, H / pg.rect.height)
            )
            a = np.frombuffer(pix.samples, np.uint8).reshape(
                pix.height, pix.width, pix.n
            )
            return np.ascontiguousarray(a[..., :3])

        g = r["georef"]
        out.append(
            {
                "key": f"p{page:02d}",
                "page": page,
                "name": f"Map No. {md.get('map_no')} ({md.get('title', '').strip()})",
                "layer": layer,
                "scale": scale,
                "doc_id": DOC_ID,
                "A": r["affine_px_to_crs"],
                "map_rect": r["map_rect"],
                "georef_method": "sheet_grid_labels (primeocr OCR)",
                "georef_res_m": None,
                "georef_inliers": [g.get("easting_inliers"), g.get("northing_inliers")],
                "classes": page_classes(r),
                "qa_warnings": [HATCH_WARNING]
                if any(p["name"] in NOT_EXTRACTED for p in r["palette"])
                else [],
                "load": load,
            }
        )
    return out


def osm_index_cached(data_root, cache_dir):
    """Junction index from the cached OSM extracts of 30 Sep 2026 (motorway-tertiary).
    Used when Overpass is unavailable; degrees are over these roads only."""
    p = os.path.join(cache_dir, "osm_junctions_major_sec.npz")
    if not os.path.exists(p):
        els = []
        for f in ("major", "sec"):
            els += read_json(os.path.join(data_root, "osm", f"{f}.json"))["elements"]
        tr = Transformer.from_crs(4326, 32643, always_xy=True)
        xy, deg = osm_junctions(els, tr)
        np.savez(p, xy=xy, deg=deg)
    z = np.load(p)
    return z["xy"], z["deg"], shapely.STRtree(shapely.points(z["xy"]))


def lpa_from_title(out_dir):
    """LPA = coloured extent of Map No. 39: closed by 60 m, holes filled, parts > 10 ha."""
    t = pq.read_table(os.path.join(out_dir, "map_lpa_raw.parquet"))
    G = shapely.from_wkb(t.column("geometry").to_numpy(zero_copy_only=False))
    u = shapely.union_all(G, grid_size=0.5).buffer(60).buffer(-60)
    parts = [shapely.Polygon(p.exterior) for p in shapely.get_parts(u) if p.area > 1e5]
    return shapely.union_all(parts)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", default=os.getenv("PLANNING_DATA_ROOT"))
    ap.add_argument("--primeocr-dir", required=True)
    ap.add_argument(
        "--source-pdf", default=None, help="Anekal MP.pdf (copied into raw/)"
    )
    ap.add_argument("--merge-only", action="store_true")
    ap.add_argument(
        "--osm-cached",
        action="store_true",
        help="OSM check on the cached major + secondary road extracts (osm/major.json, sec.json) "
        "instead of fetching all roads from Overpass",
    )
    ap.add_argument("--sheet", default=None, help=argparse.SUPPRESS)
    args = ap.parse_args()
    t0 = time.time()
    raw = os.path.join(args.data_root, "raw", PLAN_ID)
    os.makedirs(raw, exist_ok=True)
    doc_path = os.path.join(raw, f"{DOC_ID}.pdf")
    if not os.path.exists(doc_path):
        if not args.source_pdf:
            sys.exit("error: pass --source-pdf the first time")
        shutil.copyfile(args.source_pdf, doc_path)
    plan = next(r for r in read_csv(PLANS_CSV) if r["plan_id"] == PLAN_ID)
    zdir = os.path.join(args.data_root, "planning", "zones")
    out_dir = os.path.join(zdir, "ank_sheets")
    os.makedirs(out_dir, exist_ok=True)
    sh = sheets(doc_path, args.primeocr_dir)
    order = sorted(sh, key=lambda s: (LAYER_RANK[s["layer"]], s["page"]))

    # LPA from the title map (unclipped run), then the OSM junction index for its box
    title = next(s for s in sh if s["layer"] == "lpa_map")
    lpa_raw = os.path.join(out_dir, "map_lpa_raw.parquet")
    if not os.path.exists(lpa_raw):
        process_sheet(
            {**title, "key": "lpa_raw"},
            plan,
            title["classes"],
            None,
            None,
            out_dir,
            MAX_RGB_DIST,
        )
    lpa = lpa_from_title(out_dir)
    shapely.prepare(lpa)
    lo = Transformer.from_crs(32643, 4326, always_xy=True).transform
    x0, y0, x1, y1 = lpa.bounds
    bb = (*lo(x0 - 500, y0 - 500), *lo(x1 + 500, y1 + 500))
    if args.osm_cached:
        osm = osm_index_cached(args.data_root, out_dir)
    else:
        osm = osm_index(args.data_root, "ank", bb, out_dir)
    print(
        f"LPA {lpa.area / 1e4:.0f} ha (plan table {TABLE_TOTAL_HA:.0f} ha); OSM junctions {len(osm[0])}",
        flush=True,
    )

    if args.sheet:  # child: one sheet, then exit (frees memory)
        s = next(x for x in sh if x["key"] == args.sheet)
        process_sheet(s, plan, s["classes"], lpa, osm, out_dir, MAX_RGB_DIST)
        return
    if not args.merge_only:
        for s in order:
            if os.path.exists(os.path.join(out_dir, f"map_{s['key']}.parquet")):
                continue
            for f in glob.glob(os.path.join(out_dir, "merged_*")) + glob.glob(
                os.path.join(out_dir, "foot_*")
            ):
                os.remove(f)  # a sheet changed: merge results are stale
            r = subprocess.run(
                [
                    sys.executable,
                    __file__,
                    "--data-root",
                    args.data_root,
                    "--primeocr-dir",
                    args.primeocr_dir,
                    "--sheet",
                    s["key"],
                ]
                + (["--osm-cached"] if args.osm_cached else []),
                check=False,
            )
            if r.returncode != 0:
                sys.exit(
                    f"sheet {s['name']} failed (exit {r.returncode}); finished sheets are kept"
                )
            m = read_json(os.path.join(out_dir, f"map_{s['key']}.json"))
            o = m["osm_junctions"]
            print(
                f"  {s['name'][:46]:46s} {s['layer']:7s} {m['m_per_px']:5.2f} m/px {m['zone_area_ha']:8.1f} ha "
                f"junctions {o.get('matched', 0)}/{o.get('sheet_junctions', 0)} rmse {o.get('rmse_m', float('nan')):.1f} m "
                f"unknown {m['unknown_px_pct']:.1f}% ({time.time() - t0:.0f}s)",
                flush=True,
            )

    # no Anekal sheet may reach 3 ground checks on the cached major roads: borrow Hoskote's
    # floor (same BMRDA drafting, OSM-checked) rather than fall back to the bare pixel size
    hsk_qa = os.path.join(zdir, "BMRDA-HSK-MP2031_qa.json")
    hsk_floor = (
        read_json(hsk_qa).get("georef_floor_m") if os.path.exists(hsk_qa) else None
    )
    metas, floor = sheet_floor(
        order,
        out_dir,
        hsk_floor,
        "floor borrowed from Hoskote (median OSM RMSE of its well-matched sheets): no Anekal "
        "sheet has 3 ground checks on the cached OSM major roads",
    )
    covs = merge(order, metas, out_dir)
    classes = order[0]["classes"]
    nz, by, layer_area, area_check = write_zones(
        os.path.join(zdir, f"{PLAN_ID}.parquet"),
        order,
        metas,
        covs,
        lpa,
        plan,
        classes,
        out_dir,
        DOC_ID,
    )
    write_lpa(
        os.path.join(zdir, f"{PLAN_ID}_lpa.parquet"),
        plan,
        "Anekal LPA (coloured extent of Map No. 39, 1:45,000)",
        lpa,
    )
    with open(doc_path, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    qa = {
        "plan_id": PLAN_ID,
        "source_sha256": sha,
        "lpa_area_ha": round(lpa.area / 1e4, 1),
        "lpa_source": "coloured extent of Map No. 39 (1:45,000), closed 60 m, holes filled",
        "plan_table_total_ha": TABLE_TOTAL_HA,
        "zones": nz,
        "zone_area_ha_by_class": {k: round(v / 1e4, 2) for k, v in sorted(by.items())},
        "table_ha_by_class": TABLE_LPA,
        "area_ha_by_layer": {k: round(v / 1e4, 1) for k, v in layer_area.items()},
        "area_check": area_check,
        "georef_floor_m": floor,
        "sheets": [{k: v for k, v in metas[s["key"]].items()} for s in order],
        "skipped_pages": "planning-district maps (Map No. 40, 44, 48, 53): primeocr georef failed",
        "params": {"MAX_RGB_DIST": MAX_RGB_DIST},
    }
    with open(os.path.join(zdir, f"{PLAN_ID}_qa.json"), "w") as f:
        json.dump(qa, f, indent=1)
    print(
        json.dumps(
            {
                k: qa[k]
                for k in (
                    "lpa_area_ha",
                    "zones",
                    "zone_area_ha_by_class",
                    "area_ha_by_layer",
                    "area_check",
                    "georef_floor_m",
                )
            },
            indent=1,
        )
    )
    print(f"done ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
