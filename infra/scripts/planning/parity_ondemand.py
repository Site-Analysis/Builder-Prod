#!/usr/bin/env python3
"""Step A parity: on-demand /zones/at answers vs the previous parquet layers, 500 parcels.

    services/planning/.venv/Scripts/python -X faulthandler parity_ondemand.py
        --old-zones C:/Users/tanny/Downloads/planning/planning/zones
        --sample C:/Users/tanny/Downloads/planning/planning/audit/http500_check.csv
        [--service http://localhost:8012] [--cadastral http://localhost:8011]

The old layers are streamed in batches; only pieces within WINDOW_M of a sample parcel are
kept (reference store, peak memory reported), and the service's own zone_hits() runs on
them. The new answers come from the running service (polled until no sheet is pending).
Compared per plan (tolerances approved 2 Oct): the set of zone_label_native; overlap_pct
within 0.5 pp; edge_distance_m within 1 m; near_edge, status, status_condition, plan_id and
sheet_doc_ids identical. Differences on sheet seams (a hit drawn from more than one sheet in
either answer) are reported separately. Nothing is written to disk; the report is printed.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(REPO, "services", "planning"))

# numpy's OpenBLAS reserves a buffer per thread (~800 MB committed on 32 CPUs); nothing here
# needs threaded BLAS, so one thread keeps the 2 GB worker / 1 GB service caps honest
for _v in (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ.setdefault(_v, "1")

import geopandas as gpd
import httpx
import numpy as np
import pyarrow.parquet as pq
import shapely
from app.services import winjob
from app.services import zones_service as zs
from app.services.store import PlanLayers

WINDOW_M = 260.0
PLANS = ["BDA-RMP2031", "BMRDA-HSK-MP2031", "BMRDA-ANK-MP2031"]
COLS = [
    "zone_uid",
    "plan_id",
    "doc_id",
    "zone_label_native",
    "zone_code_native",
    "class_norm",
    "status",
    "status_label",
    "status_condition",
    "note",
    "cartographic",
    "source_layer",
    "sheet",
    "qa",
]


def parcels(sample: str, cadastral: str) -> list[dict]:
    seen, out = set(), []
    with open(sample, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            k = (r["key"], r["survey"])
            if k in seen:
                continue
            seen.add(k)
            d, t, h, v = r["key"].split("/")
            q = {"dist": d, "taluk": t, "hobli": h, "vlg": v, "survey": r["survey"]}
            g = httpx.get(f"{cadastral}/data", params=q, timeout=120).json()
            if not g.get("features"):
                continue
            geom, _props = zs.parcel_geometry(g)
            out.append({**q, "geom": geom, "pc": r.get("plan_coverage")})
    return out


def reference(old_zones: str, sample: list[dict]) -> tuple[dict, int]:
    """plan_id -> PlanLayers built from the old parquet pieces near the sample parcels."""
    wins = np.array([p["geom"].buffer(WINDOW_M).envelope for p in sample], dtype=object)
    tree = shapely.STRtree(wins)
    out, peak = {}, 0
    for plan_id in PLANS:
        path = os.path.join(old_zones, f"{plan_id}.parquet")
        if not os.path.exists(path):
            continue
        pf = pq.ParquetFile(path)
        cols = [c for c in COLS if c in pf.schema_arrow.names] + ["geometry"]
        keep_t, keep_g = [], []
        for b in pf.iter_batches(batch_size=40_000, columns=cols):
            g = shapely.from_wkb(
                b.column(b.schema.get_field_index("geometry")).to_numpy(
                    zero_copy_only=False
                )
            )
            hit = np.unique(tree.query(g, predicate="intersects")[0])
            if len(hit):
                keep_t.append(b.take(hit).drop_columns(["geometry"]).to_pandas())
                keep_g.append(g[hit])
            del g, b
            peak = max(peak, winjob.process_peak_bytes()[1])
        if not keep_t:
            continue
        import pandas as pd

        df = pd.concat(keep_t, ignore_index=True)
        if "source_layer" not in df.columns:
            df["source_layer"] = "composite"
        if "sheet" not in df.columns:
            df["sheet"] = None
        gdf = gpd.GeoDataFrame(df, geometry=np.concatenate(keep_g), crs=32643)
        _ = gdf.sindex
        lay = PlanLayers(plan_id, gdf, None, {}, {})
        lp = os.path.join(old_zones, f"{plan_id}_lpa.parquet")
        if os.path.exists(lp):
            t = pq.read_table(lp)
            lpa = shapely.union_all(
                shapely.from_wkb(t.column("geometry").to_numpy(zero_copy_only=False))
            )
            lay.outer_boundary = lpa.boundary
        out[plan_id] = lay
        print(f"reference {plan_id}: {len(gdf)} pieces near the sample", flush=True)
    return out, peak


def new_answer(service: str, p: dict, wait_s: float = 1800) -> dict:
    q = {k: p[k] for k in ("dist", "taluk", "hobli", "vlg", "survey")}
    t0 = time.time()
    while True:
        r = httpx.get(f"{service}/zones/at", params=q, timeout=600)
        r.raise_for_status()
        a = r.json()
        if not a.get("pending_sheets"):
            a["_seconds"] = round(time.time() - t0, 1)
            return a
        if time.time() - t0 > wait_s:
            a["_timeout"] = True
            return a
        time.sleep(5)


def compare(old_hits: list[dict], new_hits: list[dict]) -> list[str]:
    diffs = []
    by_plan = {}
    for side, hits in (("old", old_hits), ("new", new_hits)):
        for h in hits:
            by_plan.setdefault(h["plan_id"], {}).setdefault(side, {})[
                h["zone_label_native"]
            ] = h
    for plan_id, sides in by_plan.items():
        o, n = sides.get("old", {}), sides.get("new", {})
        if set(o) != set(n):
            diffs.append(f"{plan_id}: labels old {sorted(o)} new {sorted(n)}")
            continue
        for lb in o:
            a, b = o[lb], n[lb]
            if abs(a["overlap_pct"] - b["overlap_pct"]) > 0.5:
                diffs.append(
                    f"{plan_id}/{lb}: overlap {a['overlap_pct']} -> {b['overlap_pct']}"
                )
            if abs(a["edge_distance_m"] - b["edge_distance_m"]) > 1.0:
                diffs.append(
                    f"{plan_id}/{lb}: edge {a['edge_distance_m']} -> {b['edge_distance_m']}"
                )
            for k in (
                "near_edge",
                "status",
                "status_condition",
                "plan_id",
                "sheet_doc_ids",
            ):
                if a.get(k) != b.get(k):
                    diffs.append(f"{plan_id}/{lb}: {k} {a.get(k)} -> {b.get(k)}")
    return diffs


def on_seam(hits: list[dict]) -> bool:
    return any(
        len({(q.get("doc_id"), q.get("sheet")) for q in h.get("sheets_qa", [])}) > 1
        for h in hits
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--old-zones", required=True)
    ap.add_argument("--sample", required=True)
    ap.add_argument("--service", default="http://127.0.0.1:8012")
    ap.add_argument("--cadastral", default="http://127.0.0.1:8011")
    ap.add_argument(
        "--plans", default=",".join(PLANS[:2]), help="plans compared (Anekal: #39)"
    )
    args = ap.parse_args()
    job_dir = os.getenv(
        "QNIT_JOB_DIR"
    )  # detached run: its log folder, locked by this PID
    if job_dir:
        with open(os.path.join(job_dir, ".lock"), "w") as f:
            f.write(str(os.getpid()))
    t0 = time.time()
    sample = parcels(args.sample, args.cadastral)
    print(
        f"{len(sample)} sample parcels with geometry ({time.time() - t0:.0f}s)",
        flush=True,
    )
    ref, ref_peak = reference(args.old_zones, sample)
    plans = args.plans.split(",")
    rows, n_cmp = [], 0
    for i, p in enumerate(sample):
        old_hits = []
        for plan_id in plans:
            lay = ref.get(plan_id)
            if lay is not None:
                h, _t = zs.zone_hits(lay, p["geom"])
                old_hits += h
        a = new_answer(args.service, p)
        new_hits = [h for h in a["zones"] if h["plan_id"] in plans]
        d = compare(old_hits, new_hits)
        n_cmp += 1
        seam = on_seam(old_hits) or on_seam(new_hits)
        rows.append(
            {
                "parcel": f"{p['dist']}/{p['taluk']}/{p['hobli']}/{p['vlg']} {p['survey']}",
                "diffs": d,
                "seam": seam,
                "seconds": a.get("_seconds"),
                "timeout": bool(a.get("_timeout")),
                "old_n": len(old_hits),
                "new_n": len(new_hits),
            }
        )
        if (i + 1) % 25 == 0:
            nd = sum(1 for r in rows if r["diffs"])
            print(
                f"  {i + 1}/{len(sample)} compared, {nd} with differences ({time.time() - t0:.0f}s)",
                flush=True,
            )
    off = [r for r in rows if r["diffs"] and not r["seam"]]
    seam = [r for r in rows if r["diffs"] and r["seam"]]
    rep = {
        "parcels": n_cmp,
        "plans": plans,
        "differ_outside_seams": len(off),
        "differ_on_seams": len(seam),
        "differ_outside_seams_pct": round(100 * len(off) / max(1, n_cmp), 2),
        "bar_2pct_pass": len(off) <= 0.02 * n_cmp,
        "timeouts": sum(r["timeout"] for r in rows),
        "reference_peak_mb": round(ref_peak / 1e6),
        "seconds": round(time.time() - t0),
        "differences": off + seam,
    }
    print("PARITY " + json.dumps(rep), flush=True)


if __name__ == "__main__":
    main()
