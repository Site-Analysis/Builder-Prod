#!/usr/bin/env python3
"""Anekal zones vs the plan's own land-use table (Map No. 39), per class, on two extents.

Usage:
    python compare_ank_table.py --data-root <dir> [--zones <parquet>] [--out <json>]

  title:  the whole extracted LPA (Map No. 39's coloured extent, 43,158 ha)
  plan:   inside BMRDA's pre-STRR Anekal LPA (40,464 ha), the extent the table's 40,230 ha
          refers to; the title map's margin outside it (2,718 ha) is left out
Prints a markdown table (diff vs the table, +-10 % bar). Hatched classes (public utility,
hillocks / quarries) are not extracted and are shown as such.
"""

import argparse
import json
import os

import numpy as np
import pyarrow.parquet as pq
import shapely

TABLE = {  # Map No. 39, urbanisable + non-urbanisable, ha
    "residential": 11230.69,
    "commercial": 768.32,
    "industrial": 5099.95,
    "public_semi_public": 840.27,
    "open_space": 2003.78,
    "public_utility": 31.16,
    "agriculture": 10891.44,
    "water": 2147.11,
    "forest": 2126.92,
    "hillock": 293.29,
    "transport": 3943.22,
}
HATCHED = ("public_utility", "hillock")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--zones", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    zdir = os.path.join(args.data_root, "planning", "zones")
    zp = args.zones or os.path.join(zdir, "BMRDA-ANK-MP2031.parquet")
    L = pq.read_table(os.path.join(zdir, "BMRDA-LPA-MAP_lpas.parquet")).to_pylist()
    B = shapely.make_valid(
        shapely.from_wkb(
            next(
                r["geometry"]
                for r in L
                if r["authority"] == "BMRDA-ANK" and r["extent"] == "pre_strr"
            )
        )
    )
    shapely.prepare(B)
    t = pq.read_table(zp, columns=["class_norm", "geometry"])
    g = shapely.from_wkb(t.column("geometry").to_numpy(zero_copy_only=False))
    cn = np.array(
        [c or "uncoloured" for c in t.column("class_norm").to_pylist()], object
    )
    a_all = shapely.area(g)
    inside = shapely.contains(B, g)
    edge = ~inside & shapely.intersects(B, g)
    a_in = np.where(inside, a_all, 0.0)
    a_in[edge] = shapely.area(shapely.intersection(g[edge], B))
    res = {}
    for c in sorted(set(cn)):
        m = cn == c
        res[c] = {
            "title_ha": float(a_all[m].sum() / 1e4),
            "plan_ha": float(a_in[m].sum() / 1e4),
        }
    print(
        "| Class | Table (ha) | Title extent (ha) | diff | Plan extent (ha) | diff | +-10 % (plan extent) |"
    )
    print("|---|---:|---:|---:|---:|---:|---|")
    for c, tab in TABLE.items():
        if c in HATCHED:
            print(
                f"| {c} | {tab:,.2f} | not extracted | | not extracted | | (hatched) |"
            )
            continue
        r = res.get(c, {"title_ha": 0.0, "plan_ha": 0.0})
        d1 = 100 * (r["title_ha"] / tab - 1)
        d2 = 100 * (r["plan_ha"] / tab - 1)
        ok = "cartographic" if c == "transport" else ("yes" if abs(d2) <= 10 else "no")
        print(
            f"| {c} | {tab:,.2f} | {r['title_ha']:,.1f} | {d1:+.1f} % | {r['plan_ha']:,.1f} | {d2:+.1f} % | {ok} |"
        )
    u = res.get("uncoloured", {"title_ha": 0, "plan_ha": 0})
    print(f"| uncoloured | | {u['title_ha']:,.1f} | | {u['plan_ha']:,.1f} | | |")
    tot = {k: sum(v[k] for v in res.values()) for k in ("title_ha", "plan_ha")}
    print(
        f"| total | 40,230.03 | {tot['title_ha']:,.1f} | | {tot['plan_ha']:,.1f} | | |"
    )
    if args.out:
        with open(args.out, "w") as f:
            json.dump({"classes": res, "totals": tot, "table": TABLE}, f, indent=1)


if __name__ == "__main__":
    main()
