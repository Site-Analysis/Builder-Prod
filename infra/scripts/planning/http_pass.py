#!/usr/bin/env python3
"""Step G (round of 3 Oct 2026): N-parcel HTTP pass against the running services.

Seeded sample (2031) of villages from authority_villages.csv, one parcel each from the
cadastral service; for each parcel: /zones/at (polled until no sheet is pending, then timed
once more), village /authority and point /authority at the parcel's centroid. Prints one
REPORT line (counts, latencies, contract-field checks); writes nothing.

    python http_pass.py [N=500]
"""

from __future__ import annotations

import collections
import csv
import json
import os
import random
import sys
import time

import httpx
import shapely

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
PL, CA = "http://127.0.0.1:8012", "http://127.0.0.1:8011"
COVERAGE = {
    "plan_loaded",
    "plan_registered_not_loaded",
    "lpa_no_zone_map",
    "authority_no_master_plan",
    "no_master_plan_found",
}


def pct(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(len(xs) * q))], 3) if xs else None


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    with open(
        os.path.join(REPO, "infra", "planning", "authority_villages.csv"),
        encoding="utf-8",
    ) as f:
        vs = list(csv.DictReader(f))
    rng = random.Random(2031)
    rng.shuffle(vs)
    c = httpx.Client(timeout=600)
    st = collections.Counter()
    lat_at, lat_auth, lat_pt = [], [], []
    problems = []
    by_cov = collections.Counter()
    t0 = time.time()
    done = 0
    for v in vs:
        if done >= n:
            break
        q = {k: v[k] for k in ("dist", "taluk", "hobli", "vlg")}
        try:
            fc = c.get(f"{CA}/data", params=q).json()
        except Exception:  # noqa: BLE001
            st["cadastral_error"] += 1
            continue
        feats = [
            f
            for f in fc.get("features") or []
            if (f.get("properties") or {}).get("survey_no")
        ]
        if not feats:
            st["village_without_parcels"] += 1
            continue
        f = rng.choice(feats)
        survey = f["properties"]["survey_no"]
        done += 1
        # /zones/at: wait for pending sheets, then time a warm call
        for _ in range(60):
            r = c.get(f"{PL}/zones/at", params={**q, "survey": survey})
            if r.status_code != 200 or not r.json().get("pending_sheets"):
                break
            st["zones_at_pending_polls"] += 1
            time.sleep(5)
        t = time.time()
        r = c.get(f"{PL}/zones/at", params={**q, "survey": survey})
        lat_at.append(time.time() - t)
        st[f"zones_at_{r.status_code}"] += 1
        if r.status_code == 200:
            j = r.json()
            for key in (
                "build_id",
                "pending_sheets",
                "village_summary",
                "disagreement_note",
            ):
                if key not in j:
                    problems.append({"parcel": {**q, "survey": survey}, "missing": key})
            st["zones_at_hits"] += len(j["zones"])
            st["zones_at_disagreement"] += bool(j.get("disagreement_note"))
            st["zones_at_unconfirmed_hits"] += sum(
                any(
                    qq.get("placement_confirmed") is False
                    for qq in z.get("sheets_qa") or []
                )
                for z in j["zones"]
            )
            for z in j["zones"]:
                if not (0 <= z["overlap_pct"] <= 100.0001):
                    problems.append(
                        {
                            "parcel": {**q, "survey": survey},
                            "bad_overlap": z["overlap_pct"],
                        }
                    )
        # village and point /authority
        t = time.time()
        a = c.get(f"{PL}/authority", params=q)
        lat_auth.append(time.time() - t)
        st[f"authority_village_{a.status_code}"] += 1
        if a.status_code == 200:
            aj = a.json()
            by_cov[aj["plan_coverage"]] += 1
            if aj["plan_coverage"] not in COVERAGE:
                problems.append({"village": q, "bad_coverage": aj["plan_coverage"]})
        g = shapely.geometry.shape(f["geometry"])
        p = g.representative_point()
        t = time.time()
        b = c.get(f"{PL}/authority", params={"lat": p.y, "lng": p.x})
        lat_pt.append(time.time() - t)
        st[f"authority_point_{b.status_code}"] += 1
        if b.status_code == 200:
            bj = b.json()
            st["point_with_village_summary"] += bj.get("village_summary") is not None
            st["point_disagreement"] += bool(bj.get("disagreement_note"))
        if done % 50 == 0:
            print(f"  {done}/{n} ({time.time() - t0:.0f} s)", flush=True)
    rep = {
        "parcels": done,
        "counts": st,
        "village_plan_coverage": by_cov,
        "zones_at_s": {
            "p50": pct(lat_at, 0.5),
            "p95": pct(lat_at, 0.95),
            "max": pct(lat_at, 1),
        },
        "authority_village_s": {"p50": pct(lat_auth, 0.5), "p95": pct(lat_auth, 0.95)},
        "authority_point_s": {
            "p50": pct(lat_pt, 0.5),
            "p95": pct(lat_pt, 0.95),
            "max": pct(lat_pt, 1),
        },
        "problems": problems[:20],
        "n_problems": len(problems),
        "seconds": round(time.time() - t0),
    }
    print("REPORT " + json.dumps(rep), flush=True)


if __name__ == "__main__":
    main()
