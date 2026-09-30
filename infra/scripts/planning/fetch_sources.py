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
DATASETS = {"bda-revised-master-plan-2031": ("BDA-RMP2031", "BDA")}

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
]
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
        return f"{plan_id}-PLU-PD{pd}", "plan_sheet", applies
    return None


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

    def sort_key(row):
        m = re.search(r"-PLU-PD(\d+)$", row["doc_id"])
        return (
            row["plan_id"],
            1 if m else 0,
            int(m.group(1)) if m else 0,
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
