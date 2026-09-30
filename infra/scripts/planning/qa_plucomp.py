#!/usr/bin/env python3
"""QA for the extracted BDA RMP 2031 zones; writes results into the QA JSON and the qa column.

Usage:
    python qa_plucomp.py --data-root <dir>      (or set PLANNING_DATA_ROOT)

Run after extract_plucomp.py and crosscheck_pdr.py. Checks:
  - area per class vs RMP 2031 Vol 3 (Master Plan Document, p105) Table 10-1, tolerance 3 %,
    on the table's own basis: NGT Buffer is a separate class there, so zone area inferred
    under the NGT hatch is excluded (the figure including it is reported alongside);
  - PDR cross-check: every PD's agreement must beat its majority-class baseline;
  - share of "uncoloured" inside the LPA (reported, not a pass/fail);
  - georeference: robust and all-points check RMSE, dropped check points and reason.
"""

import argparse
import json
import os
import sys

import pyarrow as pa
import pyarrow.parquet as pq

PLAN_ID = "BDA-RMP2031"
DOC_ID = "BDA-RMP2031-PLUCOMP"
TOLERANCE = 0.03
# RMP 2031 (Draft) Vol 3 Master Plan Document, Table 10-1 Proposed Land Use Area Statement (ha)
TABLE_10_1_HA = {
    "residential": 42477.08,
    "commercial": 2473.74,
    "industrial": 4256.47,
    "public_semi_public": 6007.85,
    "defence": 4356.28,
    "public_utility": 471.15,
    "open_space": 3734.55,
    "transport": 11834.34,
    "forest": 577.15,
    "water+stream": 3393.56,
    "ngt_buffer": 8848.90,
    "agriculture": 32266.00,
}


def read_json(path):
    with open(path) as f:
        return json.load(f)


def with_qa(table, qa_struct):
    i = table.schema.get_field_index("qa")
    meta = table.schema.metadata
    t = table.set_column(i, "qa", pa.array([qa_struct] * table.num_rows))
    return t.replace_schema_metadata(meta)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", default=os.getenv("PLANNING_DATA_ROOT"))
    args = ap.parse_args()
    if not args.data_root:
        sys.exit("error: pass --data-root or set PLANNING_DATA_ROOT")
    zdir = os.path.join(args.data_root, "planning", "zones")
    qa = read_json(os.path.join(zdir, f"{PLAN_ID}_qa.json"))
    geo = read_json(os.path.join(args.data_root, "georef", f"{DOC_ID}.json"))
    xc = read_json(os.path.join(zdir, f"{PLAN_ID}_pdr_crosscheck.json"))
    zones_path = os.path.join(zdir, f"{PLAN_ID}.parquet")
    zt = pq.read_table(zones_path)

    by = {}
    for r in zt.select(["class_norm", "inferred_under_hatch", "area_m2"]).to_pylist():
        k = r["class_norm"]
        k = "water+stream" if k in ("water", "stream") else k
        a = by.setdefault(k, [0.0, 0.0])
        a[1 if r["inferred_under_hatch"] else 0] += r["area_m2"] / 1e4
    by["ngt_buffer"] = [qa["ngt_overlay_excl_visible_water_ha"], 0.0]
    rows, failed = [], []
    for k, target in TABLE_10_1_HA.items():
        out, under = by.get(k, [0.0, 0.0])
        diff = (out - target) / target
        ok = abs(diff) <= TOLERANCE
        rows.append(
            {
                "class": k,
                "table_ha": target,
                "extracted_ha": round(out, 1),
                "diff": round(diff, 4),
                "pass": ok,
                "incl_under_hatch_ha": round(out + under, 1),
                "incl_under_hatch_diff": round((out + under - target) / target, 4),
            }
        )
        if not ok:
            failed.append(k)
    xfail = [r["pd"] for r in xc if r["agreement"] <= r["majority_class_share"]]
    qa_failures = []
    if failed:
        qa_failures.append("area_totals: " + ", ".join(failed))
    if xfail:
        qa_failures.append(
            "pdr_crosscheck below baseline: PD " + ", ".join(map(str, xfail))
        )

    sheet = dict(qa["sheet_qa"])
    sheet["qa_failures"] = qa_failures
    sheet["legend_check"] = "warn"  # legend defaults pending SME review
    qa["sheet_qa"] = sheet
    qa["area_check"] = {
        "source": "RMP 2031 Vol 3 Table 10-1 (p105)",
        "tolerance": TOLERANCE,
        "rows": rows,
    }
    qa["pdr_crosscheck"] = xc
    qa["georef"] = {
        "georef_rmse_m": geo["georef_rmse_m"],
        "georef_rmse_all_m": geo["georef_rmse_all_m"],
        "check_points": geo["check_points"]["n"],
        "check_points_dropped": geo["check_points_dropped"],
        "check_points_dropped_reason": geo["check_points_dropped_reason"],
        "m_per_px": geo["m_per_px"],
        "method": geo["georef_method"],
    }
    with open(os.path.join(zdir, f"{PLAN_ID}_qa.json"), "w") as f:
        json.dump(qa, f, indent=1)
    pq.write_table(with_qa(zt, sheet), zones_path, compression="zstd")
    ov_path = os.path.join(zdir, f"{PLAN_ID}_overlays.parquet")
    if os.path.exists(ov_path):
        pq.write_table(
            with_qa(pq.read_table(ov_path), sheet), ov_path, compression="zstd"
        )

    print("class | table ha | extracted ha | diff | pass | incl. under-hatch ha | diff")
    for r in rows:
        print(
            f"{r['class']} | {r['table_ha']:,.0f} | {r['extracted_ha']:,.0f} | {r['diff']:+.1%} | "
            f"{'yes' if r['pass'] else 'no'} | {r['incl_under_hatch_ha']:,.0f} | {r['incl_under_hatch_diff']:+.1%}"
        )
    print("qa_failures:", qa_failures)


if __name__ == "__main__":
    main()
