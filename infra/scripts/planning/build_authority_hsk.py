#!/usr/bin/env python3
"""Add the Hoskote LPA villages to infra/planning/authority_villages.csv.

Usage:
    python build_authority_hsk.py --data-root <dir> --cadastral-dir <cadastral_lake_v2>

Two sources, as for the BDA table:
  - text: Annexure-1 of the Hoskote Master Plan report (BMRDA-HSK-MP2031-MPR, pp. 280-287),
    the villages of the LPA as declared in 2006 (316, before 51 went to the STRR LPA in 2016);
  - spatial: share of each village's parcel area inside the LPA polygon from Map No. 19
    (<data-root>/planning/zones/BMRDA-HSK-MP2031_lpa.parquet).
Coverage rules follow build_authority.py (full > 98 %, or listed and >= 90 %; partial from
2 %, an unlisted village needs 5 %). A village listed in 2006 but outside the revised LPA is
none, with a note that it most likely moved to the STRR LPA.

Rows are written only where the BDA table says coverage none; a village claimed by both
is left to BDA and reported as a conflict. Writes <data-root>/planning/authority_hsk_report.json.
"""

import argparse
import csv
import json
import os
import re
import sys

import pyarrow.parquet as pq
import pymupdf
import shapely

sys.path.insert(0, os.path.dirname(__file__))
from build_authority import norm, sim, village_names, village_shares

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
AUTH_CSV = os.path.join(REPO, "infra", "planning", "authority_villages.csv")
PLAN_ID = "BMRDA-HSK-MP2031"
AUTHORITY = "BMRDA-HSK"
ANNEX_PAGES = range(279, 287)  # 0-based: report pages 280-287
FULL_PCT, NOISE_PCT, UNLISTED_PCT, LISTED_FULL_PCT = 98.0, 2.0, 5.0, 90.0
MATCH_MIN = 0.86
NOT_NAMES = re.compile(
    r"^(ANNEXURE|Name of|HOSKOTE TALUK|BANGALORE EAST TALUK)", re.IGNORECASE
)


def annexure_names(mpr_path):
    """Village names from Annexure-1 (village column at x ~ 381 pt). Lines starting with '('
    continue the name above; a bare 'Plantation (B)' joins the previous name."""
    doc = pymupdf.open(mpr_path)
    out = []
    for pn in ANNEX_PAGES:
        lines = []
        for b in doc[pn].get_text("dict")["blocks"]:
            for ln in b.get("lines", []):
                t = " ".join(s["text"] for s in ln["spans"]).strip()
                if t and 375 < ln["bbox"][0] < 470 and not NOT_NAMES.match(t):
                    lines.append(((ln["bbox"][1] + ln["bbox"][3]) / 2, t))
        merged = []
        for _y, t in sorted(lines):
            if merged and (t.startswith("(") or t.lower().startswith("plantation")):
                merged[-1] = merged[-1] + " " + t
            else:
                merged.append(t)
        out += merged
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--cadastral-dir", required=True)
    args = ap.parse_args()
    zdir = os.path.join(args.data_root, "planning", "zones")
    lpa = shapely.from_wkb(
        pq.read_table(
            os.path.join(zdir, f"{PLAN_ID}_lpa.parquet"), columns=["geometry"]
        )
        .column("geometry")
        .to_numpy(zero_copy_only=False)
    )[0]
    names = village_names(args.cadastral_dir)
    cand = {
        k: v
        for k, v in names.items()
        if (k[0] == "21" and v["taluk"] == "HOSKOTE")
        or (k[0] == "20" and "BIDARAHALLI" in v["hobli"].upper())
    }
    text = annexure_names(
        os.path.join(args.data_root, "raw", PLAN_ID, f"{PLAN_ID}-MPR.pdf")
    )
    listed, unmatched = {}, []
    for t in text:
        best = max(cand, key=lambda k: sim(norm(t), norm(cand[k]["village"])))
        s = sim(norm(t), norm(cand[best]["village"]))
        if s >= MATCH_MIN:
            listed.setdefault(best, (t, s))
        else:
            unmatched.append(t)
    print(
        f"Annexure-1: {len(text)} names, {len(listed)} matched (>= {MATCH_MIN}), {len(unmatched)} unmatched"
    )
    shares = village_shares(args.cadastral_dir, lpa)

    with open(AUTH_CSV, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    fields = list(rows[0].keys())
    by_key = {(r["dist"], r["taluk"], r["hobli"], r["vlg"]): r for r in rows}
    added, conflicts, counts = [], [], {"full": 0, "partial": 0, "none": 0}
    for k in sorted(
        set(cand) | {k for k, v in shares.items() if v["share"] >= NOISE_PCT}
    ):
        share = shares.get(k, {}).get("share")
        t = listed.get(k)
        if share is None:
            cov = "full" if t else "none"
            note = "no parcel geometry; coverage from the Annexure-1 list" if t else ""
        elif share > FULL_PCT or (t and share >= LISTED_FULL_PCT):
            cov = "full"
            note = (
                ""
                if share > FULL_PCT
                else f"boundary drawing differs at the edge (map share {share:.1f}%)"
            )
        elif share >= (NOISE_PCT if t else UNLISTED_PCT):
            cov = "partial"
            note = "" if t else f"not in the Annexure-1 list; map share {share:.1f}%"
        else:
            cov = "none"
            note = (
                f"listed in the 2006 LPA (Annexure-1) but outside the revised LPA on Map No. 19 "
                f"(map share {share:.1f}%); most likely moved to the STRR LPA (2016)"
                if t
                else ""
            )
        if cov == "none" and not note:
            continue
        cur = by_key.get(k)
        if cur and cur["coverage"] in ("full", "partial"):
            if cov != "none":
                conflicts.append(
                    {
                        "key": "/".join(k),
                        "village": cur["village_name"],
                        "bda": cur["coverage"],
                        "hoskote": cov,
                        "hoskote_share": share,
                    }
                )
            continue
        counts[cov] += 1
        row = {f_: "" for f_ in fields} | {
            "dist": k[0],
            "taluk": k[1],
            "hobli": k[2],
            "vlg": k[3],
            "village_name": names[k]["village"],
            "authority": AUTHORITY if cov != "none" else "",
            "plan_ids": PLAN_ID if cov != "none" else "",
            "coverage": cov,
            "share_pct": "" if share is None else round(share, 2),
            "source": "both"
            if (t and share is not None and share >= NOISE_PCT)
            else ("text" if t else "spatial"),
            "text_coverage": "listed (Annexure-1)" if t else "",
            "text_name": t[0] if t else "",
            "mismatch_note": note,
        }
        by_key[k] = row
        added.append(row)
    with open(AUTH_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(by_key.values())
    rep = {
        "annexure_names": len(text),
        "matched": len(listed),
        "unmatched": unmatched,
        "rows_written": len(added),
        "coverage": counts,
        "conflicts_with_bda": conflicts,
        "lpa_km2": lpa.area / 1e6,
    }
    with open(
        os.path.join(args.data_root, "planning", "authority_hsk_report.json"), "w"
    ) as f:
        json.dump(rep, f, indent=1)
    print(
        json.dumps(
            {k: (v if not isinstance(v, list) else len(v)) for k, v in rep.items()},
            indent=1,
        )
    )


if __name__ == "__main__":
    main()
