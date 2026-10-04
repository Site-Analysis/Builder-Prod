#!/usr/bin/env python3
"""Pick 5 real parcels for the step 1.8 acceptance pack (BDA RMP 2031, Draft).

Usage:
    python acceptance_pick.py --data-root <dir> --cadastral-dir <cadastral_lake_v2> [--out picks.json]

Cases:
    deep            one zone, 100 % of the parcel, parcel edge > 60 m from the zone edge
    straddle        two zones, each > 30 %, no inferred zone, no NGT buffer within 30 m
    stream_ngt      NGT buffer covers 10-90 % of the parcel, stream centreline within 20 m
    partial_village village with coverage partial (share 30-80 %), parcel inside the LPA 30-300 m from its edge
    outside_bda     village with coverage none, parcel 20-150 m outside the LPA

Parcels come straight from the cadastral parquets with the (Northing, Easting) swap. Only
survey numbers that are unique in their village and look like 12/*/3 are used, because
/zones/at unions all parts with the same number. Seeded, so reruns give the same picks.
"""

import argparse
import csv
import json
import os
import random
import re

import pyarrow.parquet as pq
import shapely

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SURVEY_RE = r"^\d+(/[*\d]+)*$"


def load(path, cols):
    t = pq.read_table(path, columns=[*cols, "geometry"])
    return {c: t.column(c).to_pylist() for c in cols}, shapely.from_wkb(
        t.column("geometry").to_numpy(zero_copy_only=False)
    )


def parcels(cad_dir, r):
    f = os.path.join(
        cad_dir,
        f"dist_{r['dist']}",
        f"taluk_{r['taluk']}",
        f"hobli_{r['hobli']}",
        f"vlg_{r['vlg']}.parquet",
    )
    t = pq.read_table(f, columns=["survey_no", "geometry"])
    sv = [str(s) if s is not None else "" for s in t.column("survey_no").to_pylist()]
    g = shapely.from_wkb(t.column("geometry").to_numpy(zero_copy_only=False))
    g = shapely.make_valid(
        shapely.transform(g, lambda xy: xy[:, ::-1])
    )  # (N, E) -> (E, N)
    counts = {}
    for s in sv:
        counts[s] = counts.get(s, 0) + 1
    out = []
    for s, geom in zip(sv, g, strict=True):
        if counts[s] == 1 and re.match(SURVEY_RE, s) and 1500 < geom.area < 30000:
            out.append((s, geom))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--cadastral-dir", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    zdir = os.path.join(args.data_root, "planning", "zones")
    Z, G = load(
        os.path.join(zdir, "BDA-RMP2031.parquet"),
        ["zone_label_native", "class_norm", "note"],
    )
    keep = [i for i, c in enumerate(Z["class_norm"]) if c != "uncoloured"]
    G, LAB, NOTE = (
        G[keep],
        [Z["zone_label_native"][i] for i in keep],
        [Z["note"][i] for i in keep],
    )
    O, OG = load(os.path.join(zdir, "BDA-RMP2031_overlays.parquet"), ["class_norm"])
    ngt = OG[[i for i, c in enumerate(O["class_norm"]) if c == "ngt_buffer"]]
    streams = OG[[i for i, c in enumerate(O["class_norm"]) if c == "stream_centreline"]]
    lpa = load(os.path.join(zdir, "BDA-RMP2031_lpa.parquet"), [])[1][0]
    ztree, ntree, stree = (
        shapely.STRtree(G),
        shapely.STRtree(ngt),
        shapely.STRtree(streams),
    )
    with open(
        os.path.join(REPO, "infra", "planning", "authority_villages.csv"),
        encoding="utf-8",
    ) as f:
        rows = list(csv.DictReader(f))

    def key(r, sv):
        return {k: r[k] for k in ("dist", "taluk", "hobli", "vlg")} | {
            "survey": sv,
            "village": r["village_name"],
            "coverage": r["coverage"],
        }

    def shares(g):
        idx = ztree.query(g, predicate="intersects")
        by = {}
        for i in idx:
            by[LAB[i]] = by.get(LAB[i], 0.0) + g.intersection(G[i]).area / g.area
        return idx, by

    pick = {}
    rnd = random.Random(args.seed)
    full = [r for r in rows if r["coverage"] == "full" and r["source"] == "both"]
    rnd.shuffle(full)
    for r in full[:40]:
        for sv, g in parcels(args.cadastral_dir, r):
            idx, s = shares(g)
            if "deep" not in pick and len(s) == 1 and max(s.values()) > 0.999:
                union = shapely.union_all(G[idx])
                if g.boundary.distance(union.boundary) > 60:
                    pick["deep"] = key(r, sv)
            if "straddle" not in pick and len(s) == 2 and min(s.values()) > 0.3:
                clean = not any(NOTE[i] for i in idx)
                if clean and not len(ntree.query(g.buffer(30), predicate="intersects")):
                    pick["straddle"] = key(r, sv)
            if "stream_ngt" not in pick:
                n = ntree.query(g, predicate="intersects")
                st = stree.query(g, predicate="dwithin", distance=20)
                if len(n) and len(st):
                    frac = g.intersection(shapely.union_all(ngt[n])).area / g.area
                    if 0.1 < frac < 0.9:
                        pick["stream_ngt"] = key(r, sv)
        if {"deep", "straddle", "stream_ngt"} <= pick.keys():
            break

    part = sorted(
        (
            r
            for r in rows
            if r["coverage"] == "partial"
            and r["share_pct"]
            and 30 < float(r["share_pct"]) < 80
        ),
        key=lambda r: abs(float(r["share_pct"]) - 55),
    )
    for r in part[:10]:
        c = [
            sv
            for sv, g in parcels(args.cadastral_dir, r)
            if g.within(lpa) and 30 < g.distance(lpa.boundary) < 300
        ]
        if c:
            pick["partial_village"] = key(r, c[0])
            break

    near = [
        r
        for r in rows
        if r["coverage"] == "none"
        and r["dist"] == "20"
        and r["share_pct"]
        and 0 < float(r["share_pct"]) < 5
    ]
    for r in near:
        c = [
            sv
            for sv, g in parcels(args.cadastral_dir, r)
            if not g.intersects(lpa) and 20 < g.distance(lpa) < 150
        ]
        if c:
            pick["outside_bda"] = key(r, c[0])
            break

    missing = {
        "deep",
        "straddle",
        "stream_ngt",
        "partial_village",
        "outside_bda",
    } - pick.keys()
    if missing:
        print("no parcel found for:", sorted(missing))
    out = args.out or os.path.join(
        args.data_root, "planning", "acceptance", "picks.json"
    )
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(pick, f, indent=1)
    print(json.dumps(pick, indent=1))


if __name__ == "__main__":
    main()
