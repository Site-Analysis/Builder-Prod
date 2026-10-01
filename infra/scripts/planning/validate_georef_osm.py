#!/usr/bin/env python3
"""Validate OSM-only georeferencing on Hoskote sheets whose true grid is known.

Usage:
    python validate_georef_osm.py --data-root <dir> [--maps 56 49 28 23 62 70]

Why: the Nelamangala Master Plan 2031 grids print no coordinates. georef_osm.py places a
sheet from OSM roads; before trusting it, the same method is run on Hoskote detail sheets
(their printed UTM grid gives the truth) and the true corner error is compared with what
the method's own checks say. Two variants:
  affine       coarse+fine placement, junction affine (fit on half), held-out check, null
  translation  coarse placement, translation from the median junction offset, null
Writes <data-root>/planning/georef_osm_validation.json.
Result on 1 Oct 2026: no sheet within 200 m; two accepted while 105 m and 14.8 km off.
So the method is not used to load Nelamangala zones (docs/plans/open-decisions.md #4).
"""

import argparse
import json
import os

import numpy as np
import pymupdf
import shapely
from extract_hoskote import class_raster, code_of, grid_fit, raster_junctions, to_ground
from georef_osm import (
    COARSE_M,
    NULL_SHIFTS_M,
    _match,
    _peak,
    _xcorr,
    apply,
    downsample,
    major,
    place,
    rasterise_lines,
    refine,
)
from pyproj import Transformer

BOX = (790000, 1435000, 830000, 1475000)  # Hoskote LPA, EPSG:32643


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--maps", type=int, nargs="*", default=[56, 49, 28, 23, 62, 70])
    args = ap.parse_args()
    doc = pymupdf.open(
        os.path.join(
            args.data_root, "raw", "BMRDA-HSK-MP2031", "BMRDA-HSK-MP2031-MP.pdf"
        )
    )
    with open(
        os.path.join(args.data_root, "osm", "hsk_roads.json"), encoding="utf-8"
    ) as f:
        els = json.load(f)["elements"]
    tr = Transformer.from_crs(4326, 32643, always_xy=True)
    OM = rasterise_lines(major(els), tr, BOX, COARSE_M)
    z = np.load(
        os.path.join(
            args.data_root, "planning", "zones", "hsk_sheets", "osm_junctions.npz"
        )
    )
    oxy, odeg, otree = z["xy"], z["deg"], shapely.STRtree(shapely.points(z["xy"]))
    sheets = os.path.join(args.data_root, "planning", "zones", "hsk_sheets")
    rows = []
    for n in args.maps:
        with open(os.path.join(sheets, f"map_{n:02d}.json"), encoding="utf-8") as f:
            m = json.load(f)
        pg = doc[m["page"] - 1]
        cls, (x0, y0, cpt, rpt) = class_raster(pg, doc)
        truth = to_ground(grid_fit(pg))
        roads = cls == code_of("transport")
        del cls
        mpx = m["m_per_px"]
        P = np.array(
            [
                [0, 0],
                [roads.shape[1], 0],
                [0, roads.shape[0]],
                [roads.shape[1], roads.shape[0]],
            ],
            float,
        )
        T = truth(np.column_stack([x0 + P[:, 0] * cpt, y0 + P[:, 1] * rpt]))
        # variant 1: affine
        A0, _ = place(roads, mpx, OM, els, tr, BOX)
        A, chk = refine(roads, mpx, A0, oxy, odeg, otree)
        rows.append(
            {
                "map": n,
                "variant": "affine",
                "accepted": chk["accepted"],
                "matches": chk.get("matched_total"),
                "null": chk.get("null_matches_mean"),
                "true_err_max_m": round(float(np.hypot(*(apply(A, P) - T).T).max()), 1),
            }
        )
        # variant 2: translation only
        iy, ix, _r = _peak(
            _xcorr(OM, downsample(roads, max(1, round(COARSE_M / mpx)))), 40
        )
        B = np.array(
            [mpx, 0, 0, -mpx, BOX[0] + ix * COARSE_M, BOX[3] - iy * COARSE_M], float
        )
        pj, pdeg = raster_junctions(roads, mpx, lambda xy: xy)
        for rad in (80, 40, 20, 10, 10):
            best = _match(apply(B, pj), pdeg, oxy, odeg, otree, rad)
            if len(best) < 5:
                break
            off = np.array(
                [oxy[b] - apply(B, pj[[a]])[0] for a, (_d, b) in best.items()]
            )
            B[4] += np.median(off[:, 0])
            B[5] += np.median(off[:, 1])
        best = _match(apply(B, pj), pdeg, oxy, odeg, otree, 10)
        null = float(
            np.mean(
                [
                    len(
                        _match(
                            apply(B + np.array([0, 0, 0, 0, dx, dy]), pj),
                            pdeg,
                            oxy,
                            odeg,
                            otree,
                            10,
                        )
                    )
                    for dx, dy in NULL_SHIFTS_M
                ]
            )
        )
        rm = (
            float(np.sqrt(np.mean([d * d for d, _ in best.values()]))) if best else None
        )
        rows.append(
            {
                "map": n,
                "variant": "translation",
                "accepted": bool(best) and len(best) >= 3 * max(null, 1) and rm <= 10,
                "matches": len(best),
                "null": round(null, 1),
                "true_err_max_m": round(float(np.hypot(*(apply(B, P) - T).T).max()), 1),
            }
        )
        print(rows[-2], rows[-1], flush=True)
    out = os.path.join(args.data_root, "planning", "georef_osm_validation.json")
    with open(out, "w") as f:
        json.dump(rows, f, indent=1)
    print(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
