#!/usr/bin/env python3
"""Step F (round of 3 Oct 2026): plan_coverage of every village under contract 1.18.

Rewrites the entries of infra/planning/authority_villages.csv in place (text only):
  - STRR and DPA (registered as having no master plan): authority_no_master_plan.
  - Magadi, Kanakapura: their plans are registered now (not loaded): plan_registered_not_loaded.
  - Every entry of a loaded plan (BDA, Hoskote, Anekal, Nelamangala) is checked against the
    zones the running planning service serves: the share of the village coloured on that plan
    (any class except "Not coloured on the plan"). Under 5 %: lpa_no_zone_map with the
    uncoloured note (#29, F2); else plan_loaded. Nelamangala outside its town grids (and
    Madhure) is lpa_no_zone_map.
Village outlines come from the cadastral service's /boundaries (parcel unions), in RAM only;
zones from the planning service's /zones (simplify 8 m). Nothing is written but the CSV.

    python coverage_118.py [--planning http://127.0.0.1:8012] [--cadastral http://127.0.0.1:8011]
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import time

import httpx
import numpy as np
import shapely
from pyproj import Transformer

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
REG = os.path.join(REPO, "infra", "planning")
CSV = os.path.join(REG, "authority_villages.csv")
TO_UTM = Transformer.from_crs(4326, 32643, always_xy=True)
TO_WGS = Transformer.from_crs(32643, 4326, always_xy=True)
ORDER = [
    "plan_loaded",
    "plan_registered_not_loaded",
    "lpa_no_zone_map",
    "authority_no_master_plan",
    "no_master_plan_found",
]
MIN_SHARE = 5.0
TILE_DEG = 0.05
NOTE_UNCOLOURED = (
    "Only {pct:.1f} % of the village is coloured on this plan (its LPA covers "
    "{share:.1f} %; the rest of that is not coloured on the plan)"
)
NOTE_NLM_NO_GRID = (
    "Final plan loaded for its town grid sheets only; it publishes no zone map for this "
    "village"
)
NOTE_NLM_OLD = "Final plan registered; its zone sheets print no coordinates"
NOTE_NO_MP = {
    "STRR": "STRR Planning Authority (GO NAI 89 BMR 2021): no master plan published, Zoning "
    "Regulations only",
    "DPA": "Doddaballapura Planning Authority: no master plan of its own published (its site "
    "links the BIAAPA Master Plan 2021 file)",
}


def utm(g):
    return shapely.transform(
        g, lambda xy: np.column_stack(TO_UTM.transform(xy[:, 0], xy[:, 1]))
    )


def village_outlines(cad: str, hoblis) -> dict:
    out = {}
    with httpx.Client(timeout=600) as c:
        for d, t, h in sorted(hoblis):
            r = c.get(f"{cad}/boundaries", params={"dist": d, "taluk": t, "hobli": h})
            r.raise_for_status()
            for f in r.json().get("features") or []:
                if f.get("geometry"):
                    g = shapely.make_valid(utm(shapely.geometry.shape(f["geometry"])))
                    out[(d, t, h, str(f["properties"]["village_code"]))] = g
    return out


class Zones:
    """Coloured zones of one plan, fetched per 0.05 deg tile from the planning service."""

    def __init__(self, base: str, plan_id: str):
        self.base, self.plan_id = base, plan_id
        self.tiles: dict[tuple[int, int], list] = {}
        self.client = httpx.Client(timeout=900)
        self.stats = collections.Counter()

    def _tile(self, i: int, j: int) -> list:
        if (i, j) in self.tiles:
            return self.tiles[(i, j)]
        bb = (i * TILE_DEG, j * TILE_DEG, (i + 1) * TILE_DEG, (j + 1) * TILE_DEG)
        while True:
            r = self.client.get(
                f"{self.base}/zones",
                params={
                    "plan_id": self.plan_id,
                    "bbox": ",".join(f"{v:.6f}" for v in bb),
                    "simplify_m": 8,
                },
            )
            self.stats["requests"] += 1
            if r.status_code != 200:
                raise RuntimeError(f"/zones {r.status_code}: {r.text[:200]}")
            j_ = r.json()
            if not j_.get("pending_sheets"):
                break
            self.stats["pending_waits"] += 1
            time.sleep(10)
        gs = [
            shapely.make_valid(utm(shapely.geometry.shape(f["geometry"])))
            for f in j_["features"]
            if (f["properties"].get("class_norm") or "") != "uncoloured"
        ]
        self.tiles[(i, j)] = gs
        return gs

    def coloured_pct(self, g) -> float:
        w, s_ = TO_WGS.transform(*g.bounds[:2])
        e, n = TO_WGS.transform(*g.bounds[2:])
        gs = []
        for i in range(int(w // TILE_DEG), int(e // TILE_DEG) + 1):
            for j in range(int(s_ // TILE_DEG), int(n // TILE_DEG) + 1):
                gs.extend(self._tile(i, j))
        if not gs:
            return 0.0
        a = np.array(gs, dtype=object)
        hit = a[shapely.STRtree(a).query(g, predicate="intersects")]
        if not len(hit):
            return 0.0
        try:
            u = shapely.union_all(shapely.make_valid(shapely.intersection(hit, g)))
        except shapely.errors.GEOSException:
            u = shapely.union_all(
                shapely.intersection(hit, g, grid_size=0.01), grid_size=0.01
            )
        return 100.0 * u.area / g.area


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--planning", default="http://127.0.0.1:8012")
    ap.add_argument("--cadastral", default="http://127.0.0.1:8011")
    ap.add_argument(
        "--plans",
        default="",
        help="only re-check entries of these loaded plans (comma list); the 1.18 text rules "
        "(STRR, DPA, Magadi, Kanakapura) always apply",
    )
    args = ap.parse_args()
    only = {p for p in args.plans.split(",") if p}
    t0 = time.time()
    with open(CSV, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
        cols = list(rows[0].keys())
    plans = httpx.get(f"{args.planning}/plans", timeout=60).json()
    loaded = {p["plan_id"] for p in plans if p.get("loaded")}
    if only:
        loaded &= only
    print(f"loaded plans: {sorted(loaded)}", flush=True)
    regs = {}
    with open(os.path.join(REG, "authorities.csv"), encoding="utf-8") as f:
        for a in csv.DictReader(f):
            regs[a["authority"]] = a
    need = {
        (r["dist"], r["taluk"], r["hobli"])
        for r in rows
        if any(
            p in loaded
            for e in json.loads(r["authorities_json"] or "[]")
            for p in e.get("plan_ids") or []
        )
    }
    print(f"village outlines for {len(need)} hoblis ...", flush=True)
    vg = village_outlines(args.cadastral, need)
    print(f"  {len(vg)} villages ({time.time() - t0:.0f} s)", flush=True)
    zones = {p: Zones(args.planning, p) for p in loaded}
    stats = collections.Counter()
    before = collections.Counter(r["plan_coverage"] for r in rows)
    for n, r in enumerate(rows):
        k = (r["dist"], r["taluk"], r["hobli"], r["vlg"])
        ents = json.loads(r["authorities_json"] or "[]")
        for e in ents:
            a = e.get("authority")
            pids = e.get("plan_ids") or []
            if a in NOTE_NO_MP:
                e["plan_coverage"] = "authority_no_master_plan"
                e["note"] = NOTE_NO_MP[a]
            elif a in ("MAGADI", "KANAKAPURA"):
                e["plan_ids"] = [regs[a]["plan_ids"]]
                e["plan_coverage"] = "plan_registered_not_loaded"
                e["note"] = regs[a]["note"]
            elif any(p in loaded for p in pids):
                madhure = e.get("plan_coverage") == "lpa_no_zone_map" and "Madhure" in (
                    e.get("note") or ""
                )
                g = vg.get(k)
                if madhure:
                    stats["madhure"] += 1
                    continue
                if g is None:
                    stats["no_outline_kept"] += 1  # e.g. Devanahalli town (F11)
                    continue
                p = next(p for p in pids if p in loaded)
                pct = zones[p].coloured_pct(g)
                e["coloured_pct"] = round(pct, 1)
                old_note = e.get("note") or ""
                if old_note.startswith(NOTE_NLM_OLD):
                    old_note = ""
                if pct < MIN_SHARE:
                    e["plan_coverage"] = "lpa_no_zone_map"
                    e["note"] = (
                        NOTE_NLM_NO_GRID
                        if a == "BMRDA-NLM"
                        else NOTE_UNCOLOURED.format(
                            pct=pct, share=e.get("share_pct") or 100.0
                        )
                    )
                    stats[f"no_zone_map_{a}"] += 1
                else:
                    e["plan_coverage"] = "plan_loaded"
                    e["note"] = old_note.split("; Only")[0] or None
                    stats[f"loaded_{a}"] += 1
        if ents:
            r["authorities_json"] = json.dumps(ents, separators=(",", ":"))
            r["plan_coverage"] = min(
                (e["plan_coverage"] for e in ents), key=ORDER.index
            )
            r["plan_ids"] = ";".join(ents[0].get("plan_ids") or [])
        if n % 200 == 0:
            print(f"  {n}/{len(rows)} ({time.time() - t0:.0f} s)", flush=True)
    with open(CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    after = collections.Counter(r["plan_coverage"] for r in rows)
    print(
        "REPORT "
        + json.dumps(
            {
                "villages": len(rows),
                "before": before,
                "after": after,
                "stats": stats,
                "zone_requests": {p: z.stats for p, z in zones.items()},
                "seconds": round(time.time() - t0),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
