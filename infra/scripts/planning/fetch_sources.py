#!/usr/bin/env python3
"""Download the OpenCity BDA RMP 2031 dataset and register each file in plan_docs.csv.

Usage:
    python fetch_sources.py --data-root <dir>      (or set PLANNING_DATA_ROOT)

Files go to <data-root>/raw/<plan_id>/<doc_id>.pdf. Each file gets one row in
infra/planning/plan_docs.csv with sha256 and retrieved_on. Status and status_label
come from infra/planning/plans.csv, never from the file name. Re-running skips files
whose sha256 already matches the register. Stdlib only.

Only the datasets in DATASETS may be fetched (2031 layers only for now).
"""

import argparse
import csv
import datetime
import hashlib
import json
import os
import re
import sys
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
PLANS_CSV = os.path.join(REPO, "infra", "planning", "plans.csv")
DOCS_CSV = os.path.join(REPO, "infra", "planning", "plan_docs.csv")

CKAN = "https://data.opencity.in/api/3/action/package_show?id="
UA = {"User-Agent": "builder-prod-planning-fetch/1.0"}

# dataset id -> (plan_id, authority)
DATASETS = {
    "bda-revised-master-plan-2031": ("BDA-RMP2031", "BDA"),
    # Older single-PDF dataset; OpenCity says it moved under the BDA dataset above
    "bengaluru-revised-master-plan-2031": ("BDA-RMP2031", "BDA"),
}

# Proposed Land Use Map figure per PD inside the Planning District Report (Vol 4):
# PD -> (PDR page, caption as printed). Each becomes its own plan_sheet row.
PDR_PLU_FIGURES = {
    1: (27, "Figure 2-2: PD 1 Proposed Land Use Map"),
    2: (38, "Figure 3-2: PD 2 Proposed Land Use Map"),
    3: (48, "Figure 4-2: PD 3 Proposed Land Use Map"),
    4: (56, "Figure 5-2: PD 4 Proposed Land Use Map"),
    5: (64, "Figure 6-2: PD 5 Proposed Land Use Map"),
    6: (75, "Figure 7-2: PD 6 Proposed Land Use Map"),
    7: (85, "Figure 8-2: PD 7 Proposed Land Use Map"),
    8: (95, "Figure 9-2: PD 08 Proposed Land Use Map"),
    9: (107, "Figure 10-2 PD 09 Proposed Land Use Map"),
    10: (116, "Figure 11-2: PD 10 Proposed Land Use Map"),
    11: (127, "Figure 12-2: PD 11 Proposed Land Use Map"),
    12: (139, "Figure 13-2: PD 12 Proposed Land Use Map"),
    13: (150, "Figure 14-2: PD 13 Proposed Land Use Map"),
    14: (162, "Figure 15-2: PD 14 Proposed Land Map"),
    15: (171, "Figure 16-2: PD 15 Proposed Land Use Map"),
    16: (179, "Figure 17-2: PD 16 Proposed Land Map"),
    17: (187, "Figure 18-2: PD 17 Proposed Land Use Map"),
    18: (195, "Figure 19-2: PD 18 Proposed Land Use Map"),
    19: (202, "Figure 20-2: PD 19 Proposed Land Use Map"),
    20: (209, "Figure 21-2: PD 20 Proposed Land Use Map"),
    21: (216, "Figure 22-2: PD 21 Proposed Land Use Map"),
    22: (222, "Figure 23-2: PD 22 Proposed Land Use Map"),
    23: (228, "Figure 24-2: PD 23 Proposed Land Use Map"),
    24: (235, "Figure 25-2: PD 24 Proposed Land Use Map"),
    25: (244, "Figure 26-2: PD 25 Proposed Land Use Map"),
    26: (252, "Figure 27-2: PD 26 Proposed Land Use Map"),
    27: (260, "Figure 28-2: PD 27 Proposed Land Use Map"),
    28: (267, "Figure 29-2: PD 28 Proposed Landuse Map"),
    29: (274, "Figure 30-2 PD 29 Proposed Land Use Map"),
    30: (283, "Figure 31-5: PD 30 Proposed Land Use Map"),
    31: (289, "Figure 32-2: PD 31 Proposed Land Use Map"),
    32: (296, "Figure 33-2: PD 32 Proposed Land Use Map"),
    33: (305, "Figure 34-2: PD 33 Proposed Land Use Map"),
    34: (313, "Figure 35-2: PD 34 Proposed Land Use Map"),
    35: (320, "Figure 36-2: PD 35 Proposed Land Use Map"),
    36: (328, "Figure 37-2: PD 36 Proposed Land Use Map"),
    37: (335, "Figure 38-2: PD 37 Proposed Land Use Map"),
    38: (341, "Figure 39-2: PD 38 Proposed Land Use Map"),
    39: (348, "Figure 40-2: PD 39 Proposed Land Use Map"),
    40: (356, "Figure 41-2: PD 40 Proposed Land Use Map"),
    41: (364, "Figure 42-2: PD 41 Proposed Land Use Map"),
    42: (372, "Figure 43-2: PD 42 Proposed Land Use Map"),
}
# Captions with typos in the source: title uses the fixed text, original kept in the title
PDR_CAPTION_FIXES = {"Proposed Land Map": "Proposed Land Use Map"}

DOC_FIELDS = [
    "doc_id",
    "authority",
    "plan_id",
    "type",
    "title",
    "status",
    "status_label",
    "go_ref",
    "go_date",
    "applies_to",
    "amends",
    "superseded_by",
    "source_url",
    "sha256",
    "retrieved_on",
    "checked",
]

# (regex on resource name after the "<plan title> - " prefix, doc suffix, type)
_FIXED = [
    (r"^Vision Document$", "VISION", "plan_report"),
    (r"^Master Plan Document$", "MPD", "plan_report"),
    (r"^Planning District Report$", "PDR", "plan_report"),
    (r"^Planning Districts Index Map$", "PDINDEX", "plan_sheet"),
    (
        r"^Existing Land Use Composite Map on Revised Master Plan 2015$",
        "ELU",
        "plan_sheet",
    ),
    (r"^Proposed Land Use Composite Map$", "PLUCOMP", "plan_sheet"),
    (r"^Zoning Regulations$", "ZR", "zr"),
    (r"^Brochure$", "BROCHURE", "reference"),
    (r"^Suggestions Form \(Official\)$", "FORM", "reference"),
    (r"^Database/Information for Preparation of RMP 2031$", "DBINFO", "plan_report"),
    (r"^BENGALURU REVISED MASTER PLAN 2031$", "OCSINGLE", "plan_report"),
]
# OpenCity calls these "Land Use Maps", but every page is titled "Existing Land Use Map"
_PD = re.compile(r"^Land Use Maps - Planning District (\d+)\s*(?:\((.*?)\)?)?\s*$")


def classify(name, plan_id):
    """Map a CKAN resource name to (doc_id, type, applies_to), or None if unknown."""
    short = name.split(" - ", 1)[1].strip() if " - " in name else name.strip()
    for pattern, suffix, doc_type in _FIXED:
        if re.match(pattern, short):
            return f"{plan_id}-{suffix}", doc_type, "BDA LPA"
    m = _PD.match(short)
    if m:
        pd = int(m.group(1))
        label = (m.group(2) or "").strip()
        applies = f"PD {pd} ({label})" if label else f"PD {pd}"
        return f"{plan_id}-ELU-PD{pd}", "plan_sheet", applies
    return None


def add_pdr_figure_rows(rows, plan_id):
    """One plan_sheet row per PDR Proposed Land Use figure, inheriting from the PDR row."""
    parent = rows.get(f"{plan_id}-PDR")
    if not parent or plan_id != "BDA-RMP2031":
        return
    for pd, (page, caption) in PDR_PLU_FIGURES.items():
        title = caption
        for wrong, right in PDR_CAPTION_FIXES.items():
            if wrong in caption:
                title = (
                    f"{caption.replace(wrong, right)} (printed caption: '{caption}')"
                )
        elu = rows.get(f"{plan_id}-ELU-PD{pd}")
        name = elu["applies_to"] if elu else f"PD {pd}"
        rows[f"{plan_id}-PDR-PLU-PD{pd}"] = {
            **parent,
            "doc_id": f"{plan_id}-PDR-PLU-PD{pd}",
            "type": "plan_sheet",
            "title": title,
            "applies_to": f"{name}; PDR page {page}",
        }


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url, dest):
    tmp = dest + ".part"
    h = hashlib.sha256()
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        for chunk in iter(lambda: r.read(1 << 20), b""):
            f.write(chunk)
            h.update(chunk)
    os.replace(tmp, dest)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", default=os.getenv("PLANNING_DATA_ROOT"))
    ap.add_argument("--dataset", default="bda-revised-master-plan-2031")
    args = ap.parse_args()
    if not args.data_root:
        sys.exit("error: pass --data-root or set PLANNING_DATA_ROOT")
    if args.dataset not in DATASETS:
        sys.exit(
            f"error: dataset {args.dataset!r} not allowed; allowed: {list(DATASETS)}"
        )

    plan_id, authority = DATASETS[args.dataset]
    plans = {p["plan_id"]: p for p in read_csv(PLANS_CSV)}
    if plan_id not in plans:
        sys.exit(f"error: {plan_id} missing from {PLANS_CSV}")
    plan = plans[plan_id]

    req = urllib.request.Request(CKAN + args.dataset, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        pkg = json.load(r)["result"]
    resources = pkg["resources"]
    print(
        f"{pkg['title']}: {len(resources)} resources (licence: {pkg.get('license_id')})"
    )

    mapped, unknown = [], []
    for res in resources:
        c = classify(res["name"], plan_id)
        (mapped if c else unknown).append((res, c))
    if unknown:
        for res, _ in unknown:
            print(f"  UNMAPPED: {res['name']!r} {res['url']}")
        sys.exit(
            "error: unmapped resources; add a rule to classify() rather than guessing"
        )
    ids = [c[0] for _, c in mapped]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        sys.exit(f"error: duplicate doc_ids {sorted(dupes)}")

    raw_dir = os.path.join(args.data_root, "raw", plan_id)
    os.makedirs(raw_dir, exist_ok=True)
    rows = {r["doc_id"]: r for r in read_csv(DOCS_CSV)}
    today = datetime.datetime.now(tz=datetime.timezone.utc).date().isoformat()
    fetched = skipped = 0

    for res, (doc_id, doc_type, applies_to) in mapped:
        dest = os.path.join(raw_dir, f"{doc_id}.pdf")
        prev = rows.get(doc_id)
        if (
            prev
            and os.path.exists(dest)
            and prev["source_url"] == res["url"]
            and sha256_of(dest) == prev["sha256"]
        ):
            skipped += 1
            digest, retrieved = prev["sha256"], prev["retrieved_on"]
        else:
            print(f"  GET {doc_id} ...", flush=True)
            digest, retrieved = download(res["url"], dest), today
            fetched += 1
        rows[doc_id] = {
            "doc_id": doc_id,
            "authority": authority,
            "plan_id": plan_id,
            "type": doc_type,
            "title": res["name"].strip(),
            "status": plan["status"],
            "status_label": plan["status_label"],
            "go_ref": plan["go_ref"],
            "go_date": plan["go_date"],
            "applies_to": applies_to,
            "amends": "",
            "superseded_by": "",
            "source_url": res["url"],
            "sha256": digest,
            "retrieved_on": retrieved,
            "checked": "unverified",
        }

    # Same file published twice (e.g. the moved dataset): say so in the title
    for doc_id, _, _ in (c for _, c in mapped):
        row = rows[doc_id]
        twins = sorted(
            r["doc_id"]
            for r in rows.values()
            if r["sha256"] == row["sha256"]
            and r["doc_id"] != doc_id
            and "-PDR-PLU-" not in r["doc_id"]
        )
        if twins and "(same file as" not in row["title"]:
            row["title"] += f" (same file as {', '.join(twins)})"

    add_pdr_figure_rows(rows, plan_id)

    def sort_key(row):
        m = re.search(r"-(ELU|PDR-PLU)-PD(\d+)$", row["doc_id"])
        return (
            row["plan_id"],
            m.group(1) if m else "",
            int(m.group(2)) if m else 0,
            row["doc_id"],
        )

    with open(DOCS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=DOC_FIELDS)
        w.writeheader()
        w.writerows(sorted(rows.values(), key=sort_key))
    print(
        f"done: {fetched} downloaded, {skipped} unchanged, {len(rows)} rows in {DOCS_CSV}"
    )


if __name__ == "__main__":
    main()
