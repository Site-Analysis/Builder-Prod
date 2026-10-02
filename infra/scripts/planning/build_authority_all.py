#!/usr/bin/env python3
"""Give every village in Bengaluru Urban (dist 20) and Rural (dist 21) a plan_coverage.

Usage:
    python build_authority_all.py --data-root <dir> --cadastral-dir <dir>

Adds three columns to infra/planning/authority_villages.csv (contract 1.16):
  plan_coverage         plan_loaded | plan_registered_not_loaded | lpa_no_zone_map |
                        no_master_plan_found (first that applies over the entries)
  authorities_json      one entry per authority covering the village (never merged)
  sources_checked_json  sources checked, always set for no_master_plan_found
and keeps the primary columns (authority, coverage, plan_ids, ...) = the largest entry.

Entries:
  - BDA and Hoskote: the rows built before (schedule + spatial rules) are kept as they are.
  - Every other LPA: share of the village's parcel area inside the LPA polygon; >= 5 % is an
    entry (partial), > 98 % full. Polygons: Hoskote / BDA / Anekal from their own plans;
    the rest from BMRDA's LPA map (current extents; lpa_map_bmrda.py).
  - Anekal: its plan extent (before STRR); a village also in the STRR LPA gets both, with a
    note that the area moved to the STRR LPA (GO NAI 89 BMR 2021) after the plan was made.
  - Nelamangala: final plan registered but its zones are not loaded (no coordinates on the
    sheets; docs/plans/open-decisions.md #4): plan_registered_not_loaded. The 37 Madhure
    villages (added 2015, no zone map in the plan): lpa_no_zone_map.
  - Villages without parcel data: the entries of a same-named village with data in the same
    taluk (newer hobli codes re-list villages); else the authority of most of the hobli's
    villages with data (>= 50 %; full if >= 80 % are fully in it); else the taluk's (>= 80 %,
    partial); each with a note; else no_master_plan_found. (LGD village polygons are not used: their source is not recorded
    in the repo, so a KGIS origin cannot be ruled out.)

Writes infra/planning/authority_villages.csv and <data-root>/planning/authority_all_report.json.
"""

import argparse
import collections
import csv
import json
import os
import re

import numpy as np
import pyarrow.parquet as pq
import shapely

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
AUTH_CSV = os.path.join(REPO, "infra", "planning", "authority_villages.csv")
SOURCES_CSV = os.path.join(REPO, "infra", "planning", "sources_checked.csv")
MIN_SHARE, FULL_SHARE, HOBLI_MAJORITY, HOBLI_VOTE = 5.0, 98.0, 0.8, 0.5
ORDER = [
    "plan_loaded",
    "plan_registered_not_loaded",
    "lpa_no_zone_map",
    "no_master_plan_found",
]
MADHURE = ("MADURE", "MADHURE")
NOTE_STRR_MOVED = (
    "Area moved to the STRR LPA (GO NAI 89 BMR 2021) after this plan was made; whether "
    "the plan still applies here is not confirmed"
)
NOTE_NLM = (
    "Final plan registered; its zone sheets print no coordinates and could not be "
    "georeferenced reliably, so its zones are not loaded"
)
NOTE_MADHURE = (
    "Added to the LPA in 2015 (UDD 141 BMR 2015, agricultural zone until the plan is "
    "revised); the plan has no zone map for it"
)
# authority -> (plan_ids, plan_coverage, note) for map-derived entries
MAP_AUTH = {
    "BMRDA-ANK": (["BMRDA-ANK-MP2031"], "plan_loaded", None),
    "STRR": ([], "lpa_no_zone_map", None),
    "BIAAPA": (["BIAAPA-MP2021"], "plan_registered_not_loaded", None),
    "BMICAPA": (["BMICAPA-ODP2004"], "plan_registered_not_loaded", None),
    "BMRDA-NLM": (["BMRDA-NLM-MP2031"], "plan_registered_not_loaded", NOTE_NLM),
    "KANAKAPURA": ([], "lpa_no_zone_map", None),
    "MAGADI": ([], "lpa_no_zone_map", None),
    "RAMANAGARA": ([], "lpa_no_zone_map", None),
    "CHANNAPATNA": ([], "lpa_no_zone_map", None),
    "GBBSC": ([], "lpa_no_zone_map", None),
}


def read_lpa(path):
    t = pq.read_table(path)
    return shapely.union_all(
        shapely.from_wkb(t.column("geometry").to_numpy(zero_copy_only=False))
    )


def hobli_names(cad_dir):
    with open(
        os.path.join(os.path.dirname(cad_dir), "echawadi_village_list.json"),
        encoding="utf-8",
    ) as f:
        data = json.load(f)
    out = {}
    for row in data.get("Vlglist", []):
        p = row.get("vlgcode", "").split(",")
        n = row.get("vlgname", "").split("|")
        if len(p) >= 4 and len(n) >= 4:
            out[(p[3], p[2], p[1])] = n[1].strip().upper()
    return out


def entry(authority, share, plan_ids, pc, note=None, source="spatial"):
    return {
        "authority": authority,
        "coverage": "full" if share > FULL_SHARE else "partial",
        "share_pct": round(share, 2),
        "plan_ids": plan_ids,
        "plan_coverage": pc,
        "note": note,
        "source": source,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--cadastral-dir", required=True)
    args = ap.parse_args()
    zdir = os.path.join(args.data_root, "planning", "zones")
    with open(AUTH_CSV, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    base_cols = [
        c
        for c in rows[0]
        if c not in ("plan_coverage", "authorities_json", "sources_checked_json")
    ]
    with open(SOURCES_CSV, encoding="utf-8", newline="") as f:
        sources = [{k: (v or None) for k, v in r.items()} for r in csv.DictReader(f)]
    hob = hobli_names(args.cadastral_dir)

    # polygons
    m = pq.read_table(os.path.join(zdir, "BMRDA-LPA-MAP_lpas.parquet"))
    mrows = m.select(["authority", "extent"]).to_pylist()
    mg = shapely.from_wkb(m.column("geometry").to_numpy(zero_copy_only=False))
    polys = {
        r["authority"]: shapely.make_valid(g)
        for r, g in zip(mrows, mg, strict=True)
        if r["extent"] == "current" and r["authority"] in MAP_AUTH
    }
    ank_plan = os.path.join(zdir, "BMRDA-ANK-MP2031_lpa.parquet")
    if os.path.exists(ank_plan):
        polys["BMRDA-ANK"] = read_lpa(ank_plan)  # the plan's own extent (before STRR)
    else:
        polys["BMRDA-ANK"] = next(
            shapely.make_valid(g)
            for r, g in zip(mrows, mg, strict=True)
            if r["authority"] == "BMRDA-ANK" and r["extent"] == "pre_strr"
        )
    polys["BMRDA-HSK"] = read_lpa(os.path.join(zdir, "BMRDA-HSK-MP2031_lpa.parquet"))
    for g in polys.values():
        shapely.prepare(g)

    # village polygons
    v = pq.read_table(os.path.join(args.data_root, "planning", "villages.parquet"))
    vg = shapely.from_wkb(v.column("geometry").to_numpy(zero_copy_only=False))
    vkeys = list(
        zip(
            *(v.column(c).to_pylist() for c in ("dist", "taluk", "hobli", "vlg")),
            strict=True,
        )
    )
    geom = {
        k: g for k, g in zip(vkeys, vg, strict=True) if g is not None and not g.is_empty
    }
    keys = list(geom)
    garr = np.array([geom[k] for k in keys], dtype=object)
    area = shapely.area(garr)
    shares = {}
    for a, P in polys.items():
        inter = shapely.area(shapely.intersection(garr, P))
        shares[a] = dict(zip(keys, 100 * inter / np.maximum(area, 1e-9), strict=True))

    out_rows, stats = [], collections.Counter()
    pending = []
    for r in rows:
        k = (r["dist"], r["taluk"], r["hobli"], r["vlg"])
        ents = []
        auth = (r.get("authority") or "").strip()
        prior = (
            json.loads(r["authorities_json"])
            if (r.get("authorities_json") or "").strip()
            else None
        )
        if prior is not None:
            # re-run: the BDA / Hoskote rows built before this script live in the JSON now
            # (the flat columns hold the largest entry); keep those, rebuild the rest
            kept = [
                e
                for e in prior
                if e["authority"] in ("BDA", "BMRDA-HSK")
                and "mismatch_note"
                in e  # rows from build_authority(_hsk); entry() has no such key
                and e.get("source") not in ("name", "hobli", "taluk")
            ]
            ents.extend(kept)
            auth = kept[0]["authority"] if kept else ""
        elif auth in ("BDA", "BMRDA-HSK") and r["coverage"] != "none":
            ents.append(
                {
                    "authority": auth,
                    "coverage": r["coverage"],
                    "share_pct": float(r["share_pct"]) if r["share_pct"] else None,
                    "plan_ids": [p for p in (r.get("plan_ids") or "").split(";") if p],
                    "plan_coverage": "plan_loaded",
                    "pd": int(r["pd"]) if r.get("pd") else None,
                    "source": r.get("source") or None,
                    "mismatch_note": r.get("mismatch_note") or None,
                    "note": None,
                }
            )
        if k in geom:
            if auth != "BMRDA-HSK" and shares["BMRDA-HSK"][k] >= MIN_SHARE:
                ents.append(
                    entry(
                        "BMRDA-HSK",
                        shares["BMRDA-HSK"][k],
                        ["BMRDA-HSK-MP2031"],
                        "plan_loaded",
                    )
                )
            in_strr = shares.get("STRR", {}).get(k, 0.0) >= MIN_SHARE
            for a, (pids, pc, note) in MAP_AUTH.items():
                s = shares[a][k]
                if s < MIN_SHARE:
                    continue
                if a == "BMRDA-NLM" and hob.get((k[0], k[1], k[2]), "") in MADHURE:
                    pc, note = "lpa_no_zone_map", NOTE_MADHURE
                if a == "BMRDA-ANK" and in_strr:
                    note = NOTE_STRR_MOVED
                ents.append(entry(a, s, pids, pc, note))
        else:
            pending.append(len(out_rows))
        out_rows.append((r, k, ents))

    # villages without parcel data: (1) a same-named village with data in the same taluk
    # (newer hobli codes re-list villages that have parcels under the old code), then
    # (2) the authority most of the hobli's villages with data fall in
    def norm(s):
        return re.sub(r"[^A-Z]", "", (s or "").upper().replace("‌", ""))

    byname = collections.defaultdict(list)
    for r2, k2, _es in out_rows:
        if k2 in geom:
            byname[(k2[0], k2[1], norm(r2["village_name"]))].append(k2)
    ents_of = {k2: es for _r2, k2, es in out_rows}
    by_hobli = collections.defaultdict(list)
    for _r, k, ents in out_rows:
        if k in geom:
            by_hobli[k[:3]].append(
                max(ents, key=lambda e: e["share_pct"] or 0.0) if ents else None
            )
    for i in pending:
        r, k, ents = out_rows[i]
        if ents:
            continue  # kept from the BDA / Hoskote rows (schedule-based)
        same = byname.get((k[0], k[1], norm(r["village_name"])), [])
        if len(same) == 1 and ents_of[same[0]]:
            twin = "/".join(same[0])
            new_ents = []
            for e0 in ents_of[same[0]]:
                e = dict(e0)
                e["source"] = "name"
                e["note"] = "; ".join(
                    x
                    for x in (
                        e0.get("note"),
                        f"No parcel data under this code; taken from same-named village {twin} in the same taluk",
                    )
                    if x
                )
                new_ents.append(e)
            out_rows[i] = (r, k, new_ents)
            stats["inferred_from_name"] += 1
            continue
        votes = collections.Counter(
            e["authority"] for e in by_hobli.get(k[:3], []) if e
        )
        n = len(by_hobli.get(k[:3], []))
        top, cnt = votes.most_common(1)[0] if votes else (None, 0)
        if top and n and cnt / n >= HOBLI_VOTE:
            base = next(e for e in by_hobli[k[:3]] if e and e["authority"] == top)
            nfull = sum(
                1
                for e in by_hobli[k[:3]]
                if e and e["authority"] == top and e["coverage"] == "full"
            )
            e = dict(base)
            e.update(
                {
                    "share_pct": None,
                    "coverage": "full" if nfull / n >= HOBLI_MAJORITY else "partial",
                    "source": "hobli",
                    "pd": None,
                    "mismatch_note": None,
                    "note": f"No parcel data for this village; authority taken from its hobli ({cnt} of {n} villages with data in this LPA, {nfull} fully)",
                }
            )
            out_rows[i] = (r, k, [e])
            stats["inferred_from_hobli"] += 1
        else:
            # (3) the taluk: one authority over >= 80 % of its villages with data
            tv = [e for k2, es in by_hobli.items() if k2[:2] == k[:2] for e in es]
            tvotes = collections.Counter(e["authority"] for e in tv if e)
            ttop, tcnt = tvotes.most_common(1)[0] if tvotes else (None, 0)
            if ttop and tv and tcnt / len(tv) >= HOBLI_MAJORITY:
                e = dict(next(e for e in tv if e and e["authority"] == ttop))
                e.update(
                    {
                        "share_pct": None,
                        "coverage": "partial",
                        "source": "taluk",
                        "pd": None,
                        "mismatch_note": None,
                        "note": f"No parcel data for this village or its hobli; authority taken from its taluk ({tcnt} of {len(tv)} villages with data in this LPA)",
                    }
                )
                out_rows[i] = (r, k, [e])
                stats["inferred_from_taluk"] += 1
            else:
                stats["no_geometry_unresolved"] += 1

    with open(AUTH_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=base_cols
            + ["plan_coverage", "authorities_json", "sources_checked_json"],
        )
        w.writeheader()
        for r, k, ents in out_rows:
            ents.sort(
                key=lambda e: -(e["share_pct"] if e["share_pct"] is not None else 100.0)
            )
            row = {c: r.get(c, "") for c in base_cols}
            if ents:
                top = ents[0]
                pc = min((e["plan_coverage"] for e in ents), key=ORDER.index)
                row.update(
                    {
                        "authority": top["authority"],
                        "coverage": top["coverage"],
                        "plan_ids": ";".join(top["plan_ids"]),
                        "share_pct": ""
                        if top["share_pct"] is None
                        else f"{top['share_pct']:.2f}",
                        "source": top.get("source") or "",
                    }
                )
                src = []
            else:
                pc = "no_master_plan_found"
                row.update({"authority": "", "coverage": "none", "plan_ids": ""})
                finding = (
                    "location outside every LPA drawn on the map"
                    if k in geom
                    else "no parcel data and the hobli is split between authorities"
                )
                src = [dict(s) for s in sources]
                for s in src:
                    if s["doc_id"] == "BMRDA-LPA-MAP":
                        s["finding"] = finding
            row["plan_coverage"] = pc
            row["authorities_json"] = (
                json.dumps(ents, separators=(",", ":")) if ents else ""
            )
            row["sources_checked_json"] = (
                json.dumps(src, separators=(",", ":")) if src else ""
            )
            w.writerow(row)
            stats[pc] += 1
            stats[f"entries_{len(ents)}"] += 1
    by_taluk = collections.defaultdict(collections.Counter)
    for r, k, ents in out_rows:
        pc = (
            min((e["plan_coverage"] for e in ents), key=ORDER.index)
            if ents
            else "no_master_plan_found"
        )
        by_taluk[f"{k[0]}/{k[1]}"][pc] += 1
    report = {
        "villages": len(out_rows),
        "with_parcel_geometry": len(geom),
        "counts": dict(stats),
        "by_taluk": {t: dict(c) for t, c in sorted(by_taluk.items())},
    }
    with open(
        os.path.join(args.data_root, "planning", "authority_all_report.json"), "w"
    ) as f:
        json.dump(report, f, indent=1)
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
