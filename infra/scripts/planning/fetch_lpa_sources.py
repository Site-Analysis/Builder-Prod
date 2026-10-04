#!/usr/bin/env python3
"""Download the BMRDA Local Planning Area master plan documents (step 1.6) and register
each file in infra/planning/plan_docs.csv.

Usage:
    python fetch_lpa_sources.py --data-root <dir> [--plans BMRDA-HSK-MP2031 ...]

Only the URLs in SOURCES are fetched: the planning authorities' own sites
(hoskote.tpa.gov.in, nelamangala.tpa.gov.in, biaapa.tpa.gov.in; plain http, as the sites
serve no https). Links were found by browsing each site's Planning / LPA map / Zonal
regulation / Reports / Government orders / Government notifications / Master plan pages on
1 Oct 2026. No KGIS, KSRSAC Dishaank or Bhoomi Land Beat in any form.

Files go to <data-root>/raw/<plan_id>/<doc_id>.<ext>; each gets one plan_docs.csv row
with sha256, source_url and retrieved_on. Status and status_label come from plans.csv,
never from a file name. Re-running skips files whose sha256 already matches. Stdlib only.
"""

import argparse
import csv
import datetime
import hashlib
import os
import re
import sys
import urllib.parse
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
PLANS_CSV = os.path.join(REPO, "infra", "planning", "plans.csv")
DOCS_CSV = os.path.join(REPO, "infra", "planning", "plan_docs.csv")
UA = {"User-Agent": "builder-prod-planning-fetch/1.0"}
ALLOWED_HOSTS = {"hoskote.tpa.gov.in", "nelamangala.tpa.gov.in", "biaapa.tpa.gov.in"}
BLOCKED = re.compile(r"kgis|dishaank|landbeat|land-beat|bhoomi", re.IGNORECASE)

HSK = "http://hoskote.tpa.gov.in/sites/hoskote.tpa.gov.in/files/"
NLM = "http://nelamangala.tpa.gov.in/sites/nelamangala.tpa.gov.in/files/"
BIA = "http://biaapa.tpa.gov.in/sites/biaapa.tpa.gov.in/files/"
NLM_MAPS = "2.%20NPA_MASTER_PLAN_FINAL-2031_MAPS(Autosaved)_"

# plan_id -> [(doc suffix, type, title as linked on the site, url)]
SOURCES = {
    "BMRDA-HSK-MP2031": [
        (
            "LPAMAP",
            "plan_sheet",
            "Hoskote Local Planning Area Map (LPA map page)",
            HSK + "Hoskote%20Local%20Planning%20Area%20Map.pdf",
        ),
        (
            "MP",
            "plan_sheet",
            "Master Plans (master plan page)",
            HSK + "Master_Plans.pdf",
        ),
        (
            "MPR",
            "plan_report",
            "MP Report final (reports page)",
            HSK + "MP%20Report%20final%20(1).compressed.pdf",
        ),
        (
            "ZR",
            "zr",
            "Zonal Regulations-2031 (zonal regulation page)",
            HSK + "Zonal%20Regulations-2031.pdf",
        ),
        (
            "ZRR",
            "zr",
            "HOSKOTE ZR REPORT -2031 final signed (zonal regulation page)",
            HSK + "HOSKOTE_ZR%20REPORT_-2031_finalsigned.pdf",
        ),
        (
            "GO-FINALORDER",
            "go",
            "final order (government orders page)",
            HSK + "final%20order.pdf",
        ),
        (
            "GO-PROCEEDINGS",
            "go",
            "Proceedings of Govt of Karnataka (government orders page)",
            HSK + "Proceedings%20of%20Govt%20of%20Karnataka.pdf",
        ),
        (
            "GO-SEC5-10",
            "go",
            "sec 5-10 (government orders page)",
            HSK + "sec%205-10.pdf",
        ),
        (
            "GO-UDD118-2003",
            "go",
            "UDD 118 Bemrupra 2003 Notification (government notifications page)",
            HSK + "UDD%20118%20Bemrupra%202003%20Notification.pdf",
        ),
        (
            "GO-NOTIF2",
            "go",
            "notification 2 (government notifications page)",
            HSK + "notification%202.pdf",
        ),
    ],
    "BMRDA-NLM-MP2031": [
        (
            "ZR",
            "zr",
            "NPA Zoning Regulation 2031 (zonal regulation page)",
            NLM + "1%20NPA_ZONING_REGULATION_2031.pdf",
        ),
        ("CIRCU", "reference", "CIRCU (master plan page)", NLM + "CIRCU.pdf"),
        (
            "GO-GOVTORDER",
            "go",
            "Govt Order (government notifications page)",
            NLM + "Govt%20Order.pdf",
        ),
        (
            "GO-PROVISIONAL",
            "go",
            "PROVISIONAL (government notifications page)",
            NLM + "PROVISIONAL.pdf",
        ),
        ("GO-FINAL", "go", "FINAL (government notifications page)", NLM + "FINAL.pdf"),
        (
            "GO-UDDREVISED",
            "go",
            "UDD REVISED (government notifications page)",
            NLM + "UDD%20REVISED.pdf",
        ),
    ]
    + [
        (
            f"MAP{n:03d}",
            "plan_sheet",
            f"NPA Master Plan Final 2031 maps, sheet {n:03d} ({'LPA map page' if n == 2 else 'master plan page'})",
            NLM + NLM_MAPS + f"{n:03d}.{ext}",
        )
        for n, ext in [
            (n, "jpg" if n in (12,) else "pdf") for n in range(2, 24) if n != 16
        ]
    ]
    + [
        (
            "MAP014J",
            "plan_sheet",
            "NPA Master Plan Final 2031 maps, sheet 014 (jpg also linked)",
            NLM + NLM_MAPS + "014.jpg",
        )
    ],
    "BIAAPA-MP2021": [
        (
            "LPAMAP",
            "plan_sheet",
            "BIAAPA new LPA (LPA map page)",
            BIA + "BIAAPA_NEW_LPA.pdf",
        ),
        (
            "MP",
            "plan_sheet",
            "MASTER-PLAN-2021 (master plan page)",
            BIA + "MASTER-PLAN-2021.pdf",
        ),
        (
            "ZR",
            "zr",
            "ZR-BIAAPA-2021 (zonal regulation page)",
            BIA + "ZR-BIAAPA-2021.pdf",
        ),
        (
            "GO-FINAL-2009",
            "go",
            "master plan final approval govt notification gazette copy 29-01-2009",
            BIA
            + "master%20plan%20final%20approval%20govt%20notification%20gazette%20copy%2029-01-2009.pdf",
        ),
        (
            "GO-PROVISIONAL-2004",
            "go",
            "provisonal MASTERPLAN APPROVAL GOVT ORDER COPY 2004",
            BIA + "provisonal%20MASTERPLAN%20APPROVAL%20GOVT%20ORDER%20COPY%202004.pdf",
        ),
        (
            "GO-LPA-1996",
            "go",
            "LPA DECLARATION GOVT ORDER COPY 12-01-1996",
            BIA + "LPA%20DECLARATION%20GOVT%20ORDER%20COPY%2012-01-1996.pdf",
        ),
    ],
}


# Per-document status where it differs from the plan's (read from the documents, 1 Oct 2026).
# A plan is final only when its approval GO is found and recorded in plans.csv; a zoning
# regulation stays draft unless a GO approves the regulations themselves.
ZR_DRAFT = {
    "status": "draft",
    "status_condition": "",
    "status_label": "Draft: published with the final plan; the GO does not name it",
    "go_ref": "",
    "go_date": "",
}
REFERENCE = {"status": "reference", "status_condition": "", "go_ref": "", "go_date": ""}
OVERRIDES = {
    "BMRDA-HSK-MP2031-ZR": ZR_DRAFT
    | {
        "note": "Cover says '(Final)', inner pages 'Master Plan (Provisional)'; final GO UDD 152 BMR 2013 (30-01-2018) approves the report and land-use maps, not the ZR"
    },
    "BMRDA-HSK-MP2031-ZRR": ZR_DRAFT
    | {
        "note": "Zonal Regulations Vol III marked Final; the final GO does not name the ZR"
    },
    "BMRDA-NLM-MP2031-ZR": ZR_DRAFT
    | {
        "note": "Final GO UDD 150 BMR 2013 (01-06-2015) approves the report and land-use maps, not the ZR"
    },
    "BMRDA-HSK-MP2031-GO-SEC5-10": REFERENCE
    | {
        "status_label": "Reference: GO UDD 53 BMR 2013 (04-03-2013) empowering BMRDA to prepare the five LPA master plans",
        "go_ref": "UDD 53 BMR 2013",
        "go_date": "2013-03-04",
    },
    "BMRDA-HSK-MP2031-GO-NOTIF2": REFERENCE
    | {
        "status_label": "Reference: notification UDD 31 BRA 2006 (19-07-2006) constituting the planning authorities",
        "go_ref": "UDD 31 BRA 2006",
        "go_date": "2006-07-19",
    },
    "BMRDA-HSK-MP2031-GO-UDD118-2003": REFERENCE
    | {
        "status_label": "Reference: LPA declaration, GO UDD 118 Bem Ru Pra 2003 (03-03-2006, per the MP report)",
        "go_ref": "UDD 118 Bem Ru Pra 2003",
        "go_date": "2006-03-03",
    },
    "BMRDA-HSK-MP2031-GO-PROCEEDINGS": REFERENCE
    | {
        "status_label": "Reference: 2006 proceedings on new integrated townships in the BMR"
    },
    "BMRDA-NLM-MP2031-GO-GOVTORDER": REFERENCE
    | {
        "status_label": "Reference: 2006 proceedings on new integrated townships in the BMR"
    },
    "BMRDA-NLM-MP2031-GO-UDDREVISED": REFERENCE
    | {
        "status_label": "Reference: notification UDD 141 BMR 2015 (08-12-2015) adding 37 villages of Madhure hobli to the LPA, agricultural zone until the plan is revised",
        "go_ref": "UDD 141 BMR 2015",
        "go_date": "2015-12-08",
    },
    "BMRDA-NLM-MP2031-GO-PROVISIONAL": {
        "status": "superseded",
        "status_label": "Superseded: provisional approval UDD 150 BMR 2013 (16-09-2013), replaced by the final GO of 01-06-2015",
        "go_ref": "UDD 150 BMR 2013",
        "go_date": "2013-09-16",
    },
    "BIAAPA-MP2021-ZR": ZR_DRAFT
    | {
        "note": "Final GO UDD 157 BMR 2005 (27-01-2009) preamble says new zoning regulations were framed for the plan; the order sanctions the master plan"
    },
    "BIAAPA-MP2021-GO-PROVISIONAL-2004": {
        "status": "superseded",
        "status_label": "Superseded: provisional approval UDD 248 Bem Ru Pra 2003 (13-09-2004), replaced by the final GO of 27-01-2009",
        "go_ref": "UDD 248 Bem Ru Pra 2003",
        "go_date": "2004-09-13",
    },
    "BIAAPA-MP2021-GO-LPA-1996": REFERENCE
    | {"status_label": "Reference: LPA declaration government order, 12-01-1996"},
    "BMRDA-NLM-MP2031-CIRCU": REFERENCE
    | {"status_label": "Reference: circular linked on the master plan page"},
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
    host = urllib.parse.urlparse(url).netloc
    if host not in ALLOWED_HOSTS or BLOCKED.search(url):
        sys.exit(f"error: {url} is not on the allowlist")
    tmp = dest + ".part"
    h = hashlib.sha256()
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=300) as r, open(tmp, "wb") as f:
        for chunk in iter(lambda: r.read(1 << 20), b""):
            f.write(chunk)
            h.update(chunk)
    os.replace(tmp, dest)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", default=os.getenv("PLANNING_DATA_ROOT"))
    ap.add_argument("--plans", nargs="+", default=list(SOURCES))
    args = ap.parse_args()
    if not args.data_root:
        sys.exit("error: pass --data-root or set PLANNING_DATA_ROOT")
    plans = {r["plan_id"]: r for r in read_csv(PLANS_CSV)}
    rows = {r["doc_id"]: r for r in read_csv(DOCS_CSV)}
    fields = list(next(iter(rows.values())).keys())
    if "status_condition" not in fields:
        fields.insert(fields.index("status_label") + 1, "status_condition")
    today = datetime.datetime.now(tz=datetime.timezone.utc).date().isoformat()
    failed = []
    for plan_id in args.plans:
        plan = plans.get(plan_id)
        if plan is None:
            sys.exit(f"error: {plan_id} is not in plans.csv")
        raw_dir = os.path.join(args.data_root, "raw", plan_id)
        os.makedirs(raw_dir, exist_ok=True)
        for suffix, doc_type, title, url in SOURCES[plan_id]:
            doc_id = f"{plan_id}-{suffix}"
            ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower() or ".pdf"
            dest = os.path.join(raw_dir, doc_id + ext)
            prev = rows.get(doc_id)
            if (
                prev
                and os.path.exists(dest)
                and prev["source_url"] == url
                and sha256_of(dest) == prev["sha256"]
            ):
                digest, retrieved = prev["sha256"], prev["retrieved_on"]
            else:
                print(f"  GET {doc_id}", flush=True)
                try:
                    digest, retrieved = download(url, dest), today
                except Exception as e:  # noqa: BLE001 - report and carry on
                    print(f"    failed: {e}", flush=True)
                    failed.append((doc_id, str(e)))
                    continue
            rows[doc_id] = {
                "doc_id": doc_id,
                "authority": plan["authority"],
                "plan_id": plan_id,
                "type": doc_type,
                "title": title,
                "status": plan["status"],
                "status_label": plan["status_label"],
                "status_condition": plan.get("status_condition", ""),
                "go_ref": plan["go_ref"],
                "go_date": plan["go_date"],
                "applies_to": plan.get("operative_for") or "",
                "amends": "",
                "superseded_by": "",
                "source_url": url,
                "sha256": digest,
                "retrieved_on": retrieved,
                "checked": "unverified",
            }
            ov = OVERRIDES.get(doc_id)
            if ov:
                rows[doc_id].update({k: v for k, v in ov.items() if k != "note"})
                if ov.get("note"):
                    rows[doc_id]["title"] = f"{title} [{ov['note']}]"
    # the same file published under two names: say so in the title
    by_hash = {}
    for r in rows.values():
        by_hash.setdefault(r["sha256"], []).append(r["doc_id"])
    for ids in by_hash.values():
        for i in ids:
            r = rows[i]
            if (
                len(ids) > 1
                and r["plan_id"] in args.plans
                and "(same file as" not in r["title"]
            ):
                r["title"] += f" (same file as {', '.join(x for x in ids if x != i)})"
    with open(DOCS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows.values())
    print(
        f"registered {sum(1 for r in rows.values() if r['plan_id'] in args.plans)} rows; failed {len(failed)}"
    )
    for d, e in failed:
        print(f"  FAILED {d}: {e}")


if __name__ == "__main__":
    main()
