#!/usr/bin/env python3
"""Seams and overlaps between the 2031 plans and the LPAs (step E).

Usage:
    python seams.py --data-root <dir>

Polygons: each loaded plan's own LPA (<plan_id>_lpa.parquet: BDA, Hoskote, Anekal) and
BMRDA's LPA map (current extents) for the rest. For every pair that overlaps: area (ha),
villages touching the overlap (village polygons from villages.parquet), and how /zones/at
answers there (both plans' hits, each with its own status, when both plans are loaded;
otherwise the loaded one). Gaps: (a) holes inside the union of all LPAs in Bengaluru
Urban + Rural; (b) slivers along shared edges: land within 100 m of two LPAs' boundaries
that is in neither.

Writes <data-root>/planning/seams_report.json.
"""

import argparse
import itertools
import json
import os

import numpy as np
import pyarrow.parquet as pq
import shapely

PLAN_LPAS = {
    "BDA": "BDA-RMP2031",
    "BMRDA-HSK": "BMRDA-HSK-MP2031",
    "BMRDA-ANK": "BMRDA-ANK-MP2031",
}
LOADED = {"BDA-RMP2031", "BMRDA-HSK-MP2031", "BMRDA-ANK-MP2031"}
SLIVER_M = 100.0


def read(path, cols=None):
    t = pq.read_table(path)
    g = shapely.from_wkb(t.column("geometry").to_numpy(zero_copy_only=False))
    return t, g


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", required=True)
    args = ap.parse_args()
    zdir = os.path.join(args.data_root, "planning", "zones")
    polys, kind = {}, {}
    for a, pid in PLAN_LPAS.items():
        p = os.path.join(zdir, f"{pid}_lpa.parquet")
        if os.path.exists(p):
            polys[a] = shapely.make_valid(shapely.union_all(read(p)[1]))
            kind[a] = f"plan LPA ({pid})"
    t, g = read(os.path.join(zdir, "BMRDA-LPA-MAP_lpas.parquet"))
    for r, geom in zip(t.select(["authority", "extent"]).to_pylist(), g, strict=True):
        if r["extent"] == "current" and r["authority"] not in polys:
            polys[r["authority"]] = shapely.make_valid(geom)
            kind[r["authority"]] = "BMRDA LPA map (current extent)"
    vt, vg = read(os.path.join(args.data_root, "planning", "villages.parquet"))
    vkeys = [
        "/".join(k)
        for k in zip(
            *(vt.column(c).to_pylist() for c in ("dist", "taluk", "hobli", "vlg")),
            strict=True,
        )
    ]
    ok = ~shapely.is_empty(vg)
    vg, vkeys = vg[ok], [k for k, o in zip(vkeys, ok, strict=True) if o]
    vtree = shapely.STRtree(vg)
    study = shapely.union_all(vg).buffer(
        0
    )  # Bengaluru Urban + Rural (villages with parcels)
    overlaps = []
    for a, b in itertools.combinations(sorted(polys), 2):
        inter = shapely.intersection(polys[a], polys[b])
        ha = inter.area / 1e4
        if ha < 0.01:
            continue
        idx = vtree.query(inter, predicate="intersects")
        vil = [vkeys[i] for i in idx if shapely.intersection(vg[i], inter).area > 100.0]
        pa_, pb = PLAN_LPAS.get(a), PLAN_LPAS.get(b)
        loaded = [p for p in (pa_, pb) if p in LOADED]
        if len(loaded) == 2:
            answer = "both plans' hits, each with its own status (never merged); /authority lists both authorities"
        elif loaded:
            answer = f"{loaded[0]} hits; /authority lists both authorities ({a}, {b})"
        else:
            answer = f"no zones loaded; /authority lists both authorities ({a}, {b})"
        overlaps.append(
            {
                "a": a,
                "b": b,
                "ha": round(ha, 1),
                "villages": len(vil),
                "village_keys": vil[:50],
                "zones_at": answer,
                "a_source": kind[a],
                "b_source": kind[b],
            }
        )
    overlaps.sort(key=lambda o: -o["ha"])
    allu = shapely.union_all(list(polys.values()))
    holes = [shapely.Polygon(r) for p in shapely.get_parts(allu) for r in p.interiors]
    holes_in = [h for h in holes if shapely.intersection(h, study).area > 1e4]
    gaps_holes = [
        {
            "ha": round(shapely.intersection(h, study).area / 1e4, 2),
            "centroid_32643": [round(c) for c in h.centroid.coords[0]],
        }
        for h in holes_in
    ]
    slivers = []
    for a, b in itertools.combinations(sorted(polys), 2):
        near = shapely.intersection(
            polys[a].boundary.buffer(SLIVER_M), polys[b].boundary.buffer(SLIVER_M)
        )
        if near.is_empty:
            continue
        gap = shapely.difference(shapely.intersection(near, study), allu)
        ha = gap.area / 1e4
        if ha >= 0.1:
            idx = vtree.query(gap, predicate="intersects")
            vil = [
                vkeys[i] for i in idx if shapely.intersection(vg[i], gap).area > 100.0
            ]
            # gap width at a point = distance to A + distance to B (10 m grid of points)
            x0, y0, x1, y1 = gap.bounds
            xx, yy = np.meshgrid(np.arange(x0, x1, 10.0), np.arange(y0, y1, 10.0))
            pts = shapely.points(xx.ravel(), yy.ravel())
            pts = pts[shapely.contains(gap, pts)]
            w = (
                shapely.distance(pts, polys[a]) + shapely.distance(pts, polys[b])
                if len(pts)
                else np.zeros(1)
            )
            slivers.append(
                {
                    "a": a,
                    "b": b,
                    "ha": round(ha, 2),
                    "villages": len(vil),
                    "village_keys": vil[:50],
                    "width_max_m": round(float(w.max()), 1),
                    "width_p95_m": round(float(np.percentile(w, 95)), 1),
                    "width_median_m": round(float(np.median(w)), 1),
                }
            )
    report = {
        "polygons": {
            k: {"source": kind[k], "km2": round(v.area / 1e6, 1)}
            for k, v in sorted(polys.items())
        },
        "overlaps": overlaps,
        "gaps_holes_inside_union": gaps_holes,
        "gaps_slivers_along_shared_edges": sorted(slivers, key=lambda s: -s["ha"]),
        "study_area_outside_every_lpa_ha": round(
            shapely.difference(study, allu).area / 1e4, 1
        ),
    }
    with open(os.path.join(args.data_root, "planning", "seams_report.json"), "w") as f:
        json.dump(report, f, indent=1)
    print(json.dumps({k: v for k, v in report.items() if k != "overlaps"}, indent=1))
    for o in overlaps:
        print(
            o["a"], o["b"], o["ha"], "ha", o["villages"], "villages", "|", o["zones_at"]
        )


if __name__ == "__main__":
    main()
