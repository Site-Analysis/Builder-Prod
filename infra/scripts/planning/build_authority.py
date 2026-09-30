#!/usr/bin/env python3
"""Village-to-authority table for the BDA Local Planning Area (build step 1.7, BDA only).

Usage:
    python build_authority.py --data-root <dir> --cadastral-dir <cadastral_lake_v2 dir>

Two independent sources, compared:
  text     RMP 2031 Vol 3 (Master Plan Document) Annexure 1 "Schedule of LPA of BDA":
           taluk, hobli, village, Full / Part village. PD from the PD Index Map table
           ("Wards and Villages within Planning District") where the village is named.
  spatial  LPA boundary (LPD BDA outline on the PLUCOMP composite, georeferenced like the
           zones) against our cadastral parcels: share_pct = parcel area inside / total.

Coverage comes from the measured share: full > 98 %, partial >= 2 %, none < 2 % (edge
noise at ~10 m georef). A village the text lists but the map puts outside stays partial
with a mismatch note. Names are matched fuzzily within Bengaluru Urban (20) and Bengaluru
Rural (21); anything not matched with confidence is reported, never guessed. No KGIS.

Writes infra/planning/authority_villages.csv, <data-root>/planning/zones/BDA-RMP2031_lpa.parquet
and <data-root>/planning/authority_report.json.
"""

import argparse
import csv
import glob
import json
import os
import re
import sys
from difflib import SequenceMatcher

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pymupdf
import shapely
from pyproj import CRS

sys.path.insert(0, os.path.dirname(__file__))
from extract_plucomp import geoparquet, lpa_polygon_page

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
OUT_CSV = os.path.join(REPO, "infra", "planning", "authority_villages.csv")
PLAN_ID = "BDA-RMP2031"
DISTS = ("20", "21")  # Bengaluru Urban, Bengaluru Rural
FULL_PCT, NOISE_PCT = 98.0, 2.0
NOISE_UNLISTED_PCT = 5.0  # a village the text does not list needs 5 % to count
EDGE_FULL_MIN = (
    90.0  # text says full and map 90-98 %: full, edge drawing differs (75-90: partial)
)
ALIAS_NAME_MIN, ALIAS_SHARE_MIN, PD_NEAR_M = 0.7, 50.0, 500.0
ALIAS_CSV = os.path.join(REPO, "infra", "planning", "village_aliases.csv")
SKIPPED: list[str] = []
MATCH_MIN, MATCH_MARGIN = 0.86, 0.04
FIELDS = [
    "dist",
    "taluk",
    "hobli",
    "vlg",
    "village_name",
    "authority",
    "plan_ids",
    "coverage",
    "share_pct",
    "pd",
    "source",
    "text_coverage",
    "text_name",
    "mismatch_note",
]


def norm(s):
    """Loose transliteration-insensitive key for Kannada place names in English."""
    s = s.lower()
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)
    s = re.sub(r"[^a-z]", "", s)
    for a, b in (
        ("halli", "hali"),
        ("hally", "hali"),
        ("palaya", "palya"),
        ("agrahara", "agrahar"),
        ("aa", "a"),
        ("ee", "i"),
        ("oo", "u"),
        ("th", "t"),
        ("dh", "d"),
        ("bh", "b"),
        ("kh", "k"),
        ("sh", "s"),
        ("ph", "p"),
        ("gh", "g"),
        ("krus", "kris"),
        ("ai", "y"),
        ("w", "v"),
        ("z", "j"),
    ):
        s = s.replace(a, b)
    return re.sub(r"(.)\1+", r"\1", s)


HOBLI_ALIASES = {"krpura": "krisnarajapura"}


def norm_hobli(s):
    """Hobli key: drop the numeric/part suffixes our data carries (K R PURA1, VARTURU2)."""
    s = re.sub(r"[\d\-\s]+$", "", s.strip())
    k = norm(s)
    k = HOBLI_ALIASES.get(k, k)
    return k[:-1] if k.endswith(("u", "a")) and len(k) > 4 else k


def sim(a, b):
    return SequenceMatcher(None, a, b).ratio()


# ---------------------------------------------------------------- text sources
def annexure_rows(mpd):
    rows = []
    for page in mpd:
        if (
            "Village / Survey" not in page.get_text()
            and "Full Village" not in page.get_text()
        ):
            continue
        for tb in page.find_tables().tables:
            for r in tb.extract():
                c = [re.sub(r"\s+", " ", x or "").strip() for x in r]
                if len(c) == 5 and re.fullmatch(r"\d+", c[0]):
                    rows.append(
                        {
                            "sl": int(c[0]),
                            "taluk": c[1],
                            "hobli": c[2],
                            "village": c[3],
                            "coverage_text": c[4],
                        }
                    )
    return rows


def pd_index(pdindex):
    """village/ward name (normalised) -> PD number, from the PD Index Map table."""
    out = {}
    for tb in pdindex[0].find_tables().tables:
        for r in tb.extract():
            cells = [re.sub(r"\s+", " ", x or "").strip() for x in r]
            for i in range(len(cells) - 1):
                if re.fullmatch(r"\d{1,2}", cells[i]) and cells[i + 1]:
                    for name in re.split(r",|\.", cells[i + 1]):
                        if name.strip():
                            out.setdefault(norm(name), int(cells[i]))
    return out


# ---------------------------------------------------------------- spatial
def village_names(cad_dir):
    path = os.path.join(
        os.path.dirname(cad_dir.rstrip("/\\")), "echawadi_village_list.json"
    )
    names = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for v in json.load(f)["Vlglist"]:
                vlg, hobli, taluk, dist = v["vlgcode"].split(",")[:4]
                parts = v["vlgname"].split("|")
                names[(dist, taluk, hobli, vlg)] = {
                    "village": parts[0].strip(),
                    "hobli": parts[1].strip() if len(parts) > 1 else "",
                    "taluk": parts[2].strip() if len(parts) > 2 else "",
                }
    return names


def village_shares(cad_dir, lpa):
    shapely.prepare(lpa)
    out = {}
    for dist in DISTS:
        for f in sorted(
            glob.glob(
                os.path.join(
                    cad_dir, f"dist_{dist}", "taluk_*", "hobli_*", "vlg_*.parquet"
                )
            )
        ):
            parts = f.replace("\\", "/").split("/")
            key = (
                dist,
                parts[-3].split("_")[1],
                parts[-2].split("_")[1],
                parts[-1][4:-8],
            )
            if "geometry" not in pq.read_schema(f).names:
                SKIPPED.append("/".join(key))  # placeholder file (no parcels scraped)
                continue
            t = pq.read_table(f, columns=["geometry", "village_name"])
            g = shapely.from_wkb(t.column("geometry").to_numpy(zero_copy_only=False))
            g = shapely.make_valid(
                _swap(g)
            )  # (Northing, Easting) -> (Easting, Northing)
            area = shapely.area(g)
            total = float(area.sum())
            if total <= 0:
                continue
            inside = shapely.contains(lpa, g)
            cut = ~inside & shapely.intersects(lpa, g)
            a_in = float(area[inside].sum()) + float(
                shapely.area(shapely.intersection(g[cut], lpa)).sum()
            )
            vn = t.column("village_name").to_pylist()
            out[key] = {
                "share": 100 * a_in / total,
                "name": next((n for n in vn if n), ""),
                "hull": shapely.convex_hull(shapely.GeometryCollection(list(g))),
            }
    return out


def _swap(g):
    """Parquets store (Northing, Easting): the [0,1,1,0,0,0] fix from CLAUDE.md."""
    return shapely.transform(g, lambda xy: xy[:, ::-1])


# ---------------------------------------------------------------- aliases
# abbreviations in our (e-Chawadi) names; KH GH is a record-type prefix, not part of the name
ABBREV = {"ma ka": "manavarthe kavalu", "kh gh": "", "a kere": "amanikere"}
# text taluk -> our taluk names (Yelahanka taluks were carved out of Bengaluru North)
TALUKS = {
    "Bengaluru North": {"BANGALORE-NORTH", "YALAHANKA", "BANGALORE NORTH(ADDITIONAL)"},
    "Bengaluru East": {"BANGALORE-EAST"},
    "Bengaluru South": {"BANGALORE-SOUTH"},
    "Anekal Taluk": {"ANEKAL"},
}
NOT_VILLAGES = ("city survey",)


def expand(our_name):
    o = " " + our_name.lower() + " "
    for a, b in ABBREV.items():
        o = o.replace(f" {a} ", f" {b} ")
    return " ".join(o.split())


def text_core(text_name):
    """Drop '(alias)' and 'urf ...' tails from a text name."""
    t = re.sub(r"\(.*?\)", " ", text_name)
    t = re.split(r"\burf\b", t, flags=re.IGNORECASE)[0]
    return " ".join(t.split())


def name_closeness(text_name, our_name):
    """Whole-name similarity, or, when our name abbreviates words to initials
    (S BINGEEPURA ~ Sarvamanya Bingipura), token by token: same token count, each
    initial is the first letter of the text word in that position, each full word
    matches its text word."""
    o = expand(our_name)
    t = text_core(text_name)
    whole = sim(norm(t), norm(o))
    tt = [w for w in re.split(r"[\s\-]+", t) if norm(w)]
    ot = [w for w in o.split() if w]
    if len(tt) != len(ot) or not any(len(w) == 1 for w in ot):
        return whole
    parts = []
    for a, b in zip(tt, ot):
        if len(b) == 1:
            if not a.lower().startswith(b):
                return whole
        else:
            parts.append(sim(norm(a), norm(b)))
    return max(whole, min(parts) if parts else 0.0)


def pd_near(key, text_pd, shares, pd_hulls):
    """In or next to the PD: our village outline within PD_NEAR_M of a village already
    matched to that PD (PD boundaries themselves are not available)."""
    if not text_pd:
        return "n/a"
    hull = shares.get(key, {}).get("hull")
    hulls = pd_hulls.get(text_pd, [])
    if hull is None or not hulls:
        return "unknown"
    d = min(hull.distance(h) for h in hulls)
    return "ok" if d <= PD_NEAR_M else f"fail ({d:.0f} m from PD {text_pd})"


def propose_aliases(unmatched_rows, names, shares, taken, pds, pd_hulls):
    """One proposal per unmatched text row. Accepted only when: name closeness
    >= ALIAS_NAME_MIN and clear of the runner-up; map share > ALIAS_SHARE_MIN;
    PD check ok where the text gives a PD, taluk agrees where it does not; and the
    village is not already taken by another match or alias."""
    out, taken = [], set(taken)
    for r in unmatched_rows:
        text_pd = pds.get(norm(r["village"]))
        base = {
            "text": r,
            "key": None,
            "closeness": 0,
            "pd_check": "",
            "text_pd": text_pd,
        }
        if r["village"].lower().startswith(NOT_VILLAGES):
            out.append(
                {
                    **base,
                    "accepted": False,
                    "evidence": "not a village (city survey sheets)",
                }
            )
            continue
        best = []
        for k, v in names.items():
            if k[0] not in DISTS:
                continue
            sh = shares.get(k, {}).get("share")
            if sh is None or sh <= ALIAS_SHARE_MIN:
                continue
            best.append((name_closeness(r["village"], v["village"]), k))
        best.sort(reverse=True)
        if not best or best[0][0] < 0.5:
            out.append(
                {
                    **base,
                    "accepted": False,
                    "evidence": "no village inside the LPA with a close name",
                }
            )
            continue
        c, k = best[0]
        runner = best[1][0] if len(best) > 1 else 0.0
        pd_check = pd_near(k, text_pd, shares, pd_hulls)
        taluk_ok = names[k]["taluk"] in TALUKS.get(r["taluk"], set())
        fails = []
        if c < ALIAS_NAME_MIN:
            fails.append(f"name not close (< {ALIAS_NAME_MIN})")
        if c - runner < 0.05:
            fails.append("runner-up name as close")
        if text_pd and pd_check != "ok":
            fails.append(f"PD {pd_check}")
        if not text_pd and not taluk_ok:
            fails.append(
                f"no PD in text and taluk differs ({r['taluk']} vs {names[k]['taluk']})"
            )
        if k in taken:
            fails.append("village already matched")
        accepted = not fails
        if accepted:
            taken.add(k)
        check = (
            f"PD {text_pd} {pd_check}"
            if text_pd
            else f"no PD in text; taluk {'agrees' if taluk_ok else 'differs'}"
        )
        out.append(
            {
                **base,
                "key": k,
                "closeness": round(c, 2),
                "pd_check": pd_check,
                "accepted": accepted,
                "evidence": f"name {c:.2f} (next {runner:.2f}); map share {shares[k]['share']:.1f}%; {check}"
                + (f"; FAIL: {', '.join(fails)}" if fails else ""),
            }
        )
    return out


# ---------------------------------------------------------------- matching
def match(text_rows, names, shares):
    # every village in our data is a candidate, with or without parcel geometry
    cands = [(k, v) for k, v in names.items() if k[0] in DISTS]
    keyed = [
        (k, norm(v["village"]), norm_hobli(v["hobli"]), norm(v["taluk"]), v)
        for k, v in cands
    ]
    results, unmatched = [], []
    for r in text_rows:
        nv, nh = norm(r["village"]), norm_hobli(r["hobli"])
        scored = []
        for k, cv, ch, _ct, _v in keyed:
            s = sim(nv, cv)
            if s < 0.7:
                continue
            hob = sim(nh, ch) if nh and ch else 0.0
            scored.append((s + (0.08 if hob >= 0.8 else 0.0), s, hob, k, cv, ch))
        scored.sort(key=lambda x: -x[0])
        tie_note = ""
        if len(scored) > 1 and (scored[0][4], scored[0][5]) == (
            scored[1][4],
            scored[1][5],
        ):
            # our village list carries the same village twice (e.g. JIGANI and JIGANI2):
            # prefer the entry with parcel geometry, then the larger share inside the LPA
            same = [x for x in scored if (x[4], x[5]) == (scored[0][4], scored[0][5])]
            same.sort(
                key=lambda x: (x[3] in shares, shares.get(x[3], {}).get("share", -1)),
                reverse=True,
            )
            rest = [x for x in scored if (x[4], x[5]) != (scored[0][4], scored[0][5])]
            scored = [same[0], *rest]
            tie_note = f"same name listed {len(same)}x in our data; picked the one with parcels / larger LPA share"
        best = scored[0] if scored else None
        second = scored[1] if len(scored) > 1 else None
        ok = (
            best
            and best[1] >= MATCH_MIN
            and (not second or best[0] - second[0] >= MATCH_MARGIN)
        )
        if ok:
            results.append((r, best[3], round(best[1], 3), tie_note))
        else:
            unmatched.append(
                {
                    "text": f"{r['village']} ({r['hobli']}, {r['taluk']})",
                    "candidates": [
                        f"{names[s[3]]['village']} ({names[s[3]]['hobli']}) {s[1]:.2f}"
                        for s in scored[:3]
                    ],
                }
            )
    return results, unmatched


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", default=os.getenv("PLANNING_DATA_ROOT"))
    ap.add_argument("--cadastral-dir", default=os.getenv("CADASTRAL_DATA_DIR"))
    args = ap.parse_args()
    if not args.data_root or not args.cadastral_dir:
        sys.exit("error: pass --data-root and --cadastral-dir")
    raw = os.path.join(args.data_root, "raw", PLAN_ID)

    # LPA polygon in EPSG:32643 (same georeference as the zones)
    with open(os.path.join(args.data_root, "georef", f"{PLAN_ID}-PLUCOMP.json")) as f:
        A = np.array(json.load(f)["affine_page_to_32643"])
    lpa_page = lpa_polygon_page(
        pymupdf.open(os.path.join(raw, f"{PLAN_ID}-PLUCOMP.pdf"))[0]
    )
    lpa = shapely.transform(
        lpa_page, lambda xy: np.column_stack([xy, np.ones(len(xy))]) @ A
    )
    lpa = shapely.make_valid(lpa)
    print(f"LPA polygon {lpa.area / 1e6:.1f} km2")

    text_rows = [
        r
        for r in annexure_rows(pymupdf.open(os.path.join(raw, f"{PLAN_ID}-MPD.pdf")))
        if r["taluk"] != "Bengaluru"
    ]
    pds = pd_index(pymupdf.open(os.path.join(raw, f"{PLAN_ID}-PDINDEX.pdf")))
    names = village_names(args.cadastral_dir)
    shares = village_shares(args.cadastral_dir, lpa)
    print(
        f"text villages {len(text_rows)}; cadastral villages {len(shares)}; PD names {len(pds)}"
    )

    matched, unmatched_text = match(text_rows, names, shares)
    by_key, dup = {}, []
    for r, k, score, _tie in matched:
        if k in by_key:
            dup.append(
                f"{r['village']} and {by_key[k][0]['village']} -> {names[k]['village']}"
            )
        by_key[k] = (r, score, _tie)

    # PD outlines proxy: villages already matched to a PD
    pd_hulls = {}
    for k, (r, _score, _tie) in by_key.items():
        pd = pds.get(norm(r["village"]))
        if pd and shares.get(k, {}).get("hull") is not None:
            pd_hulls.setdefault(pd, []).append(shares[k]["hull"])

    # text-listed villages the map puts outside: is a same-named village in another hobli
    # the right one? Decide with the text's PD.
    zero_checks = []
    for k, (r, score, tie) in list(by_key.items()):
        sh = shares.get(k, {}).get("share")
        if sh is None or sh >= NOISE_PCT:
            continue
        text_pd = pds.get(norm(r["village"]))
        taluks = TALUKS.get(r["taluk"], set())

        def fits(kk, text_pd=text_pd, taluks=taluks):
            # inside the LPA and in/next to the text's PD, or, with no PD in the text,
            # in the text's taluk (a village without parcel geometry has no share to test)
            s = shares.get(kk, {}).get("share")
            if text_pd:
                return (s or 0) > ALIAS_SHARE_MIN and pd_near(
                    kk, text_pd, shares, pd_hulls
                ) == "ok"
            return names[kk]["taluk"] in taluks and (s is None or s > ALIAS_SHARE_MIN)

        alts = [
            kk
            for kk, v in names.items()
            if kk[0] in DISTS
            and kk != k
            and kk not in by_key
            and norm(expand(v["village"])) == norm(r["village"])
        ]
        passing = [kk for kk in alts if fits(kk)]
        line = {
            "text": r["village"],
            "text_taluk": r["taluk"],
            "text_hobli": r["hobli"],
            "text_pd": text_pd,
            "from": "/".join(k),
            "from_name": f"{names[k]['village']} ({names[k]['hobli']}, {names[k]['taluk']})",
            "from_share": round(sh, 1),
        }
        if len(passing) == 1:
            kk = passing[0]
            s2 = shares.get(kk, {}).get("share")
            why = (
                f"PD {text_pd} ok" if text_pd else f"same taluk ({names[kk]['taluk']})"
            )
            del by_key[k]
            by_key[kk] = (
                r,
                score,
                f"re-pointed from {'/'.join(k)} (map share {sh:.1f}%) to the same-named village; {why}"
                + ("; no parcel geometry" if s2 is None else f"; map share {s2:.1f}%"),
            )
            zero_checks.append(
                {
                    **line,
                    "result": "re-pointed",
                    "to": "/".join(kk),
                    "to_name": f"{names[kk]['village']} ({names[kk]['hobli']}, {names[kk]['taluk']})",
                    "to_share": None if s2 is None else round(s2, 1),
                    "why": why,
                }
            )
        elif not passing and not text_pd and names[k]["taluk"] not in taluks:
            del by_key[k]
            zero_checks.append(
                {
                    **line,
                    "result": "dropped",
                    "why": f"fuzzy match in the wrong taluk ({names[k]['taluk']}); no same-named village in {r['taluk']}",
                }
            )
        else:
            zero_checks.append(
                {
                    **line,
                    "result": "kept, flagged"
                    if not passing
                    else f"ambiguous: {len(passing)} pass",
                    "alternatives": ["/".join(a) for a in alts],
                }
            )

    matched_texts = {id(v[0]) for v in by_key.values()}
    unmatched_rows = [r for r in text_rows if id(r) not in matched_texts]
    aliases = propose_aliases(unmatched_rows, names, shares, set(by_key), pds, pd_hulls)
    with open(ALIAS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(
            [
                "text_name",
                "text_hobli",
                "text_taluk",
                "text_pd",
                "dist",
                "taluk",
                "hobli",
                "vlg",
                "our_name",
                "our_hobli",
                "share_pct",
                "name_closeness",
                "pd_check",
                "accepted",
                "evidence",
            ]
        )
        for a in aliases:
            k = a["key"]
            w.writerow(
                [
                    a["text"]["village"],
                    a["text"]["hobli"],
                    a["text"]["taluk"],
                    a["text_pd"] or "",
                    *(k if k else ("", "", "", "")),
                    names[k]["village"] if k else "",
                    names[k]["hobli"] if k else "",
                    round(shares[k]["share"], 1) if k else "",
                    a["closeness"],
                    a["pd_check"],
                    "yes" if a["accepted"] else "no",
                    a["evidence"],
                ]
            )
    for a in aliases:
        if a["accepted"] and a["key"] not in by_key:
            by_key[a["key"]] = (a["text"], a["closeness"], f"alias: {a['evidence']}")

    rows, disagree = [], []
    # every village in our list for these districts gets a row (404 means unknown codes)
    keys = set(shares) | set(by_key) | {k for k in names if k[0] in DISTS}
    for k in sorted(keys, key=lambda kk: tuple(int(x) for x in kk)):
        s = shares.get(k, {"share": None, "name": ""})
        share = round(s["share"], 2) if s["share"] is not None else None
        if share is None:
            spatial_cov = "no_data"
        else:
            spatial_cov = (
                "full"
                if share > FULL_PCT
                else "partial"
                if share >= NOISE_PCT
                else "none"
            )
        t = by_key.get(k)
        text_cov = (
            ("full" if t[0]["coverage_text"].lower().startswith("full") else "partial")
            if t
            else ""
        )
        cov, note = spatial_cov, ""
        tie = t[2] if t else ""
        if spatial_cov == "no_data":
            cov = text_cov or "none"
            note = "no parcel geometry in our data" + (
                "; coverage from the text list" if t else ""
            )
        elif t and spatial_cov == "none":
            cov = "partial"
            note = f"text lists it ({t[0]['coverage_text'][:60]}); map share {share}%"
        elif (
            t
            and text_cov == "full"
            and spatial_cov == "partial"
            and share >= EDGE_FULL_MIN
        ):
            cov = "full"
            note = f"boundary drawing differs at the edge (map share {share}%)"
        elif t and text_cov == "full" and spatial_cov == "partial":
            note = f"text says full village; map share {share}%"
        elif t and text_cov == "partial" and spatial_cov == "full":
            note = f"text says part village ({t[0]['coverage_text'][:60]}); map share {share}%"
        elif not t and spatial_cov == "partial" and share < NOISE_UNLISTED_PCT:
            cov = "none"
            note = f"edge noise: not in the text list and map share {share}% (< {NOISE_UNLISTED_PCT:.0f}%)"
        elif not t and spatial_cov != "none":
            note = f"not in the text list; map share {share}%"
        if note and spatial_cov != "no_data":
            disagree.append(
                {
                    "key": "/".join(k),
                    "village": names.get(k, {}).get("village", s["name"]),
                    "note": note,
                }
            )
        authority = "BDA" if cov != "none" else ""
        vname = names.get(k, {}).get("village") or s["name"]
        pd = (
            pds.get(norm(t[0]["village"]))
            if t
            else pds.get(norm(vname))
            if authority
            else None
        )
        rows.append(
            {
                "dist": k[0],
                "taluk": k[1],
                "hobli": k[2],
                "vlg": k[3],
                "village_name": vname,
                "authority": authority,
                "plan_ids": PLAN_ID if authority else "",
                "coverage": cov,
                "share_pct": "" if share is None else share,
                "pd": pd or "",
                "source": "both"
                if t and spatial_cov not in ("none", "no_data")
                else "text"
                if t
                else "spatial"
                if authority
                else "",
                "text_coverage": text_cov,
                "text_name": t[0]["village"] if t else "",
                "mismatch_note": "; ".join(x for x in (note, tie) if x),
            }
        )
    matched_keys = set(by_key)
    unmatched_spatial = [
        {
            "key": f"{r['dist']}/{r['taluk']}/{r['hobli']}/{r['vlg']}",
            "village": r["village_name"],
            "share_pct": r["share_pct"],
        }
        for r in rows
        if r["share_pct"] != ""
        and r["share_pct"] >= NOISE_PCT
        and (r["dist"], r["taluk"], r["hobli"], r["vlg"]) not in matched_keys
    ]
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    geoparquet(
        os.path.join(args.data_root, "planning", "zones", f"{PLAN_ID}_lpa.parquet"),
        pa.table(
            {
                "plan_id": [PLAN_ID],
                "authority": ["BDA"],
                "label": ["LPA of BDA (LPD BDA boundary, PLUCOMP)"],
            }
        ),
        np.array([lpa], dtype=object),
        CRS.from_epsg(32643),
    )
    counts = {
        c: sum(1 for r in rows if r["coverage"] == c)
        for c in ("full", "partial", "none")
    }
    report = {
        "counts": counts,
        "text_rows": len(text_rows),
        "cadastral_files_without_geometry": SKIPPED,
        "text_matched": len(matched),
        "unmatched_text": unmatched_text,
        "duplicate_matches": dup,
        "unmatched_spatial": unmatched_spatial,
        "disagreements": disagree,
        "zero_share_checks": zero_checks,
        "aliases_proposed": len(aliases),
        "aliases_accepted": sum(1 for a in aliases if a["accepted"]),
        "unmatched_text_after_aliases": [
            a["text"]["village"] for a in aliases if not a["accepted"]
        ],
    }
    with open(
        os.path.join(args.data_root, "planning", "authority_report.json"), "w"
    ) as f:
        json.dump(report, f, indent=1)
    print(
        json.dumps(
            {k: (v if not isinstance(v, list) else len(v)) for k, v in report.items()},
            indent=1,
        )
    )


if __name__ == "__main__":
    main()
