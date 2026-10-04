#!/usr/bin/env python3
"""Register the sources found in the "every village gets an answer" round (step D / H).

Usage:
    python register_round2.py --data-root <dir> [--anekal-pdf "<path to Anekal MP.pdf>"]

Adds rows to infra/planning/plans.csv and plan_docs.csv (stdlib only). Downloads only from
the hosts below (no KGIS, KSRSAC Dishaank or Bhoomi Land Beat); files go to
<data-root>/raw/<plan_id>/<doc_id>.pdf with sha256 and retrieved_on. Idempotent: rows are
replaced by doc_id / plan_id.

  BMRDA-ANK-MP2031   Anekal LPA Master Plan 2031 sheets ("Anekal MP.pdf", 22 sheets), from
                     the "DTCP Docs" Drive folder (URL unverified; anekal.tpa.gov.in is India-only)
  STRR-LPA           STRR Planning Authority (GO NAI 89 BMR 2021): LPA map, zoning
                     regulations, road management map, revised 90 m IRR; no master plan
  BMRDA-LPAS         BMRDA's "Local Planning Areas in Bengaluru Metropolitan Region" map and the
                     2009 Gazette approving the Interim Master Plans (linked on the STRR site)
  BMRDA-RSP2031      BMR Revised Structure Plan 2031, draft report (OpenCity)
  BMICAPA-ODP2004    BMICAPA Outline Development Plan, PD sheets of 12-04-2004 (no GO found)
  DPA-LPA            Doddaballapura Planning Authority notifications (scanned Kannada)
"""

import argparse
import csv
import datetime as dt
import hashlib
import os
import shutil
import sys
import urllib.parse
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
PLANS_CSV = os.path.join(REPO, "infra", "planning", "plans.csv")
DOCS_CSV = os.path.join(REPO, "infra", "planning", "plan_docs.csv")
ALLOWED = {
    "strrpa.karnataka.gov.in",
    "doddaballapur.tpa.gov.in",
    "www.bmicapa.tpa.gov.in",
    "data-opencity.sgp1.cdn.digitaloceanspaces.com",
}
TODAY = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).date().isoformat()  # IST
STRR_U = "https://strrpa.karnataka.gov.in/uploads/"
DPA_U = "http://doddaballapur.tpa.gov.in/sites/doddaballapur.tpa.gov.in/files/"
BMIC_U = "http://www.bmicapa.tpa.gov.in/sites/bmicapa.tpa.gov.in/files/"

PLANS = [
    {
        "plan_id": "STRR-LPA",
        "authority": "STRR",
        "name": "Satellite Town Ring Road LPA (no master plan published)",
        "horizon": "",
        "status": "reference",
        "status_label": "Reference: LPA constituted (GO NAI 89 BMR 2021), no master plan published",
        "status_condition": "",
        "go_ref": "NAI 89 BMR 2021",
        "go_date": "2021-07-20",
        "operative_for": "",
        "checked": "verified",
        "notes": "strrpa.karnataka.gov.in (checked 1 Oct 2026): LPA map, Zoning Regulations, road management map, revised 90 m IRR. The link titled 'Interim Master Plan (Final)' is the 2009 Gazette approving the Interim Master Plans of Anekal, Hoskote, Nelamangala, Magadi and Kanakapura LPAs, not an STRR plan.",
    },
    {
        "plan_id": "BMRDA-LPAS",
        "authority": "BMRDA",
        "name": "BMRDA register of Local Planning Areas",
        "horizon": "",
        "status": "reference",
        "status_label": "Reference: LPA extents and constitution",
        "status_condition": "",
        "go_ref": "",
        "go_date": "",
        "operative_for": "",
        "checked": "verified",
        "notes": "BMRDA map 'Local Planning Areas in Bengaluru Metropolitan Region' (1:125,000) with each LPA's area; used for village-to-authority only (lpa_map_bmrda.py).",
    },
    {
        "plan_id": "BMRDA-RSP2031",
        "authority": "BMRDA",
        "name": "BMR Revised Structure Plan 2031 (draft report)",
        "horizon": "2031",
        "status": "draft",
        "status_label": "Draft (regional structure plan, not a zoning plan)",
        "status_condition": "",
        "go_ref": "",
        "go_date": "",
        "operative_for": "",
        "checked": "unverified",
        "notes": "Checked for village lists per LPA (1 Oct 2026): none in the report (taluk-level census tables only).",
    },
    {
        "plan_id": "BMICAPA-ODP2004",
        "authority": "BMICAPA",
        "name": "BMICAPA Outline Development Plan (proposed land use by planning district)",
        "horizon": "",
        "status": "draft",
        "status_label": "Status unconfirmed, GO not found yet",
        "status_condition": "",
        "go_ref": "",
        "go_date": "",
        "operative_for": "",
        "checked": "unverified",
        "notes": "52 PD sheets dated 12-04-2004 on bmicapa.tpa.gov.in/en/master-plan (checked 1 Oct 2026); the approval GO is not on the site. Registered only, not loaded.",
    },
    {
        "plan_id": "DPA-LPA",
        "authority": "DPA",
        "name": "Doddaballapura Planning Authority (no master plan published)",
        "horizon": "",
        "status": "reference",
        "status_label": "Reference: planning authority constituted 2020, no master plan published",
        "status_condition": "",
        "go_ref": "",
        "go_date": "",
        "operative_for": "",
        "checked": "unverified",
        "notes": "doddaballapur.tpa.gov.in government notifications (scanned Kannada, not read): constitution and village-list notifications. No master plan on the site (checked 1 Oct 2026).",
    },
]
DOCS = [
    # (doc_id, plan_id, type, title, source_url, status, status_label, go_ref, go_date)
    (
        "STRR-LPA-MAP",
        "BMRDA-LPAS",
        "reference",
        "Local Planning Areas in Bengaluru Metropolitan Region (1:125,000)",
        STRR_U + "media_to_upload1756733506.pdf",
        "reference",
        "Reference: LPA extents (BMRDA map)",
        "",
        "",
    ),
    (
        "STRR-ZR",
        "STRR-LPA",
        "zr",
        "STRR Zoning Regulations",
        STRR_U + "media_to_upload1756983013.pdf",
        "reference",
        "Reference: zoning regulations of the STRR LPA (no master plan)",
        "",
        "",
    ),
    (
        "STRR-ROAD-MGMT",
        "STRR-LPA",
        "reference",
        "STRR road management map",
        STRR_U + "media_to_upload1785323055.pdf",
        "reference",
        "Reference: road management map",
        "",
        "",
    ),
    (
        "STRR-IRR90-REVISED",
        "STRR-LPA",
        "reference",
        "Revised 90 m IRR",
        STRR_U + "media_to_upload1784189746.pdf",
        "reference",
        "Reference: revised 90 m intermediate ring road",
        "",
        "",
    ),
    (
        "STRR-IMP-FINAL",
        "BMRDA-LPAS",
        "go",
        "Gazette 2009: Interim Master Plans of Anekal, Hoskote, Nelamangala, Magadi, Kanakapura approved (linked as 'Interim Master Plan (Final)')",
        STRR_U + "media_to_upload1784273630.pdf",
        "superseded",
        "Superseded: interim master plans (2009), replaced by the 2031 master plans where approved",
        "",
        "",
    ),
    (
        "BMRDA-RSP2031-DRAFT",
        "BMRDA-RSP2031",
        "plan_report",
        "BMR Revised Structure Plan 2031, draft report",
        "https://data-opencity.sgp1.cdn.digitaloceanspaces.com/Documents/Recent/Revised-Structure-Plan-2031-Draft-Report.pdf",
        "draft",
        "Draft (regional structure plan, not a zoning plan)",
        "",
        "",
    ),
    (
        "DPA-NOTIF-2020-03-11",
        "DPA-LPA",
        "go",
        "Govt notification NaAaE 20 BMR 2020 (11-03-2020)",
        DPA_U + "Govt%20Notification%20NaAaE20BMR2020%20Dated%2011.03.2020.pdf",
        "reference",
        "Reference: notification (scanned Kannada, not read)",
        "NaAaE 20 BMR 2020",
        "2020-03-11",
    ),
    (
        "DPA-NOTIF-VILLAGES",
        "DPA-LPA",
        "go",
        "Govt notification: village list (IZ - Nelamangala)",
        DPA_U
        + "Govt%20Notification%20%20IZ-%20Nelamangala%20%20Village%20List_compressed.pdf",
        "reference",
        "Reference: village list notification (scanned Kannada, not read)",
        "",
        "",
    ),
    (
        "DPA-ORDER-2019",
        "DPA-LPA",
        "go",
        "DPA order copy (2019-12-04)",
        DPA_U + "DPA%20Order%20Copy%20%20%20%20%2020191204_15583665.pdf",
        "reference",
        "Reference: order (scanned Kannada, not read)",
        "",
        "",
    ),
    (
        "BMICAPA-LPA-2011",
        "BMICAPA-ODP2004",
        "go",
        "Revised LPA notification (20-11-2011)",
        BMIC_U + "REVISED%2020-11-2011%20LPA%20Notification.pdf",
        "reference",
        "Reference: LPA notification",
        "",
        "2011-11-20",
    ),
    (
        "BMICAPA-CONSTITUTED-1999",
        "BMICAPA-ODP2004",
        "go",
        "Authority constituted on 05-10-1999",
        BMIC_U + "AUTHORITY%20CONSTITUTED%20ON%2005.10.1999.pdf",
        "reference",
        "Reference: constitution of the authority",
        "",
        "1999-10-05",
    ),
    (
        "BMICAPA-ODP2004-PD01",
        "BMICAPA-ODP2004",
        "plan_sheet",
        "Proposed land use map of planning district 1L (12-04-2004)",
        BMIC_U + "Sheet5_BMICAPA_PD1L%20(1).pdf",
        "draft",
        "Status unconfirmed, GO not found yet",
        "",
        "",
    ),
]
DOCS += [
    (
        f"BMICAPA-ODP2004-PD{n:02d}",
        "BMICAPA-ODP2004",
        "plan_sheet",
        f"Proposed land use map of planning district sheet {n} (12-04-2004)",
        BMIC_U + f"{n}.pdf",
        "draft",
        "Status unconfirmed, GO not found yet",
        "",
        "",
    )
    for n in range(2, 53)
]


def sha256_of(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def download(url, dest):
    host = urllib.parse.urlparse(url).hostname
    if host not in ALLOWED:
        sys.exit(f"error: {host} is not an allowed source host")
    if os.path.exists(dest):
        return
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 builder-prod-planning/1.0"}
    )
    with urllib.request.urlopen(req, timeout=300) as r, open(dest + ".part", "wb") as f:
        shutil.copyfileobj(r, f)
    os.replace(dest + ".part", dest)


def upsert(path, rows, key):
    with open(path, encoding="utf-8", newline="") as f:
        rd = csv.DictReader(f)
        fields, old = rd.fieldnames, list(rd)
    new_keys = {r[key] for r in rows}
    out = [r for r in old if r[key] not in new_keys] + [
        {c: r.get(c, "") for c in fields} for r in rows
    ]
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--anekal-pdf", default=None)
    args = ap.parse_args()
    upsert(PLANS_CSV, PLANS, "plan_id")
    rows = []
    for doc_id, plan_id, typ, title, url, status, label, go_ref, go_date in DOCS:
        dest = os.path.join(args.data_root, "raw", plan_id, f"{doc_id}.pdf")
        legacy = os.path.join(args.data_root, "raw", "STRR", f"{doc_id}.pdf")
        if not os.path.exists(dest) and os.path.exists(legacy):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copyfile(legacy, dest)
        try:
            download(url, dest)
        except Exception as e:  # noqa: BLE001
            print(f"  {doc_id}: download failed ({e}); not registered")
            continue
        rows.append(
            {
                "doc_id": doc_id,
                "authority": next(
                    p["authority"] for p in PLANS if p["plan_id"] == plan_id
                ),
                "plan_id": plan_id,
                "type": typ,
                "title": title,
                "status": status,
                "status_label": label,
                "status_condition": "",
                "go_ref": go_ref,
                "go_date": go_date,
                "applies_to": "",
                "amends": "",
                "superseded_by": "",
                "source_url": url,
                "sha256": sha256_of(dest),
                "retrieved_on": TODAY,
                "checked": "verified" if status != "draft" else "unverified",
            }
        )
    ank = os.path.join(
        args.data_root, "raw", "BMRDA-ANK-MP2031", "BMRDA-ANK-MP2031-MP.pdf"
    )
    if not os.path.exists(ank) and args.anekal_pdf:
        os.makedirs(os.path.dirname(ank), exist_ok=True)
        shutil.copyfile(args.anekal_pdf, ank)
    if os.path.exists(ank):
        rows.append(
            {
                "doc_id": "BMRDA-ANK-MP2031-MP",
                "authority": "BMRDA-ANK",
                "plan_id": "BMRDA-ANK-MP2031",
                "type": "plan_sheet",
                "title": "Anekal LPA Master Plan 2031, proposed land use sheets (Map No. 39-60, 22 sheets)",
                "status": "final",
                "status_label": "Final, sanctioned",
                "status_condition": "",
                "go_ref": "UDD 151 BMR 2013",
                "go_date": "2014-09-03",
                "applies_to": "Anekal LPA",
                "amends": "",
                "superseded_by": "",
                "source_url": "https://drive.google.com/drive/folders/1WUA1jwwuvEA1mB8U8U-oRlcOY1DQTYaM",
                "sha256": sha256_of(ank),
                "retrieved_on": TODAY,
                "checked": "unverified",
            }
        )
    upsert(DOCS_CSV, rows, "doc_id")
    print(f"plans: {len(PLANS)} upserted; docs: {len(rows)} upserted")


if __name__ == "__main__":
    main()
