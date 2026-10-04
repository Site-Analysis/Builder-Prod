# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Plan roads next to a parcel (contract 1.20, `abutting_roads`; open-decisions #72-#77).

Roads are the pre-drawn plan road layers that `infra/scripts/planning/build_roads.py`
publishes with the map tiles (`manifest.json` -> "roads" -> plan_id -> url): ROW corridors,
centrelines and width labels in WGS84. The service reads the manifest named by
`PLANNING_ROADS_SOURCE` (an https URL, or a local path / file:// URL when
`PLANNING_ALLOW_FILE_SOURCES=1`), loads each plan's roads into memory once (re-read hourly)
and never writes them to disk.

For each road near a parcel: plan ROW and status, frontage, the parcel area inside the ROW
(widening), an existing-width estimate from the cadastral map (the gap between the parcels
either side of the plan centreline), the width used (declared, else estimate), its Zonal
Regulations band and the ZR rows for that band (`infra/planning/zr_road_rules.json`).
"""

from __future__ import annotations

import collections
import gzip
import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request

import httpx
import numpy as np
import shapely
from pyproj import Transformer

log = logging.getLogger(__name__)

_TO_METRIC = Transformer.from_crs(4326, 32643, always_xy=True)
NEAR_M = 15.0  # corridor within this distance of the parcel = the parcel fronts it
MAX_ROADS = 5
STATION_M = 10.0
RAY_M = 40.0
MIN_GAP_M = 3.0
RELOAD_S = 3600
VILLAGE_CACHE = 12  # village parcel sets and hobli outlines
BANDS = [
    (9.0, "upto_9"),
    (12.0, "over_9_to_12"),
    (18.0, "over_12_to_18"),
    (24.0, "over_18_to_24"),
    (float("inf"), "over_24"),
]
BAND_ORDER = [b for _w, b in BANDS]
STATUS_TEXT = {
    "to_be_widened": "Existing road to be widened (plan ROW)",
    "proposed": "Proposed road (plan ROW)",
    "existing_row_stated": "Existing road; the plan states its ROW",
    "ring_proposed": "Proposed ring / radial road (plan ROW from the sheet legend)",
    "existing_drawn": "Existing road drawn on the plan (no width label)",
    "plan_row_stated": "Road on the plan; the plan states its ROW (existing or proposed not shown)",
}
CONF_RANK = {"HIGH": 2, "MEDIUM": 1, "LOW": 0}
# road statuses whose grey band on the plan is the existing road (to scale; #78)
DRAWN_EXISTING = ("to_be_widened", "existing_row_stated", "existing_drawn")
PLAN_DRAWN_NOTE = (
    "Width of the road as drawn on the plan's road sheets. It agrees with the plan's labels on "
    "12-24 m roads but not with the cadastral road land, so it is shown, not used; enter the "
    "measured width."
)
GAP_NOTE = (
    "Gap between the cadastral parcels either side of the plan centreline, every 10 m "
    "along the frontage. Unsurveyed strips count as road, so it reads wide; confirm on site."
)


def band_of(width_m: float | None) -> str | None:
    if width_m is None:
        return None
    return next(b for w, b in BANDS if width_m <= w)


def _metric(g):
    return shapely.transform(
        g, lambda xy: np.column_stack(_TO_METRIC.transform(xy[:, 0], xy[:, 1]))
    )


def _read(src: str) -> bytes:
    if src.startswith(("http://", "https://")):
        with urllib.request.urlopen(src, timeout=120) as r:
            return r.read()
    if os.getenv("PLANNING_ALLOW_FILE_SOURCES") != "1":
        raise ValueError(f"file sources are off: {src}")
    if src.startswith("file:"):
        src = urllib.request.url2pathname(urllib.parse.urlparse(src).path)
    with open(src, "rb") as f:
        return f.read()


def _json(data: bytes):
    """JSON, gzipped or not (the published roads files are gzip)."""
    if data[:2] == bytes((0x1F, 0x8B)):  # gzip magic
        data = gzip.decompress(data)
    return json.loads(data)


class PlanRoads:
    """One plan's roads in EPSG:32643: corridors (with their centreline when drawn)."""

    def __init__(self, plan_id: str, meta: dict, fc: dict):
        self.plan_id = plan_id
        self.meta = meta
        cent: dict[str, shapely.Geometry] = {}
        corr = []
        for f in fc.get("features") or []:
            p = f.get("properties") or {}
            g = f.get("geometry")
            if not g:
                continue
            if p.get("part") == "centreline" and p.get("rid") is not None:
                cent[str(p["rid"])] = _metric(shapely.from_geojson(json.dumps(g)))
            elif p.get("part") == "corridor":
                corr.append(
                    (
                        _metric(
                            shapely.make_valid(shapely.from_geojson(json.dumps(g)))
                        ),
                        p,
                    )
                )
        self.corridors = [g for g, _p in corr]
        self.props = [p for _g, p in corr]
        self.centrelines = [cent.get(str(p.get("rid"))) for p in self.props]
        self.tree = shapely.STRtree(self.corridors) if self.corridors else None


class RoadStore:
    def __init__(
        self, source: str | None, register_dir: str, docs: dict, cadastral_url: str
    ):
        self.source = source
        self.docs = docs
        self.cadastral_url = cadastral_url.rstrip("/")
        self.plans: dict[str, PlanRoads] = {}
        self.loaded_at = 0.0
        self.error: str | None = None
        self._lock = threading.Lock()
        self._villages: collections.OrderedDict = collections.OrderedDict()
        path = os.path.join(register_dir, "zr_road_rules.json")
        try:
            with open(path, encoding="utf-8") as f:
                self.zr = json.load(f)
        except OSError:
            self.zr = {}

    # --- loading ------------------------------------------------------------------------

    def ensure(self) -> None:
        if not self.source:
            return
        with self._lock:
            if self.plans and time.time() - self.loaded_at < RELOAD_S:
                return
            if self.error and time.time() - self.loaded_at < 300:
                return  # a failed read is retried after 5 min, not on every request
            try:
                man = json.loads(_read(self.source))
                base = self.source.rsplit("/", 1)[0]
                plans = {}
                for plan_id, meta in (man.get("roads") or {}).items():
                    url = meta.get("url") or ""
                    if url and "://" not in url and not os.path.isabs(url):
                        url = f"{base}/{url}"
                    plans[plan_id] = PlanRoads(plan_id, meta, _json(_read(url)))
                self.plans = plans
                self.error = None
                log.info(
                    "plan roads: %s",
                    {k: len(v.corridors) for k, v in plans.items()},
                )
            except Exception as ex:  # noqa: BLE001 - roads are an add-on; answers go on
                self.error = str(ex)[:300]
                log.warning("plan roads not loaded: %s", self.error)
            self.loaded_at = time.time()

    # --- cadastral gap ------------------------------------------------------------------

    def _cadastral(self, path: str, params: dict, authorization: str | None):
        headers = {"Authorization": authorization} if authorization else {}
        tok = os.getenv("PLANNING_CADASTRAL_TOKEN")
        if tok and not authorization:
            headers["Authorization"] = f"Bearer {tok}"
        try:
            r = httpx.get(
                f"{self.cadastral_url}{path}",
                params=params,
                headers=headers,
                timeout=60,
            )
            r.raise_for_status()
            return r.json()
        except Exception as ex:  # noqa: BLE001 - no estimate without the village map
            log.warning("cadastral %s %s: %s", path, params, ex)
            return None

    def _cached(self, key, load):
        if key in self._villages:
            self._villages.move_to_end(key)
            return self._villages[key]
        val = load()
        self._villages[key] = val
        while len(self._villages) > VILLAGE_CACHE:
            self._villages.popitem(last=False)
        return val

    def _village_geoms(self, key: tuple, authorization: str | None) -> list:
        def load():
            fc = self._cadastral(
                "/data",
                {"dist": key[0], "taluk": key[1], "hobli": key[2], "vlg": key[3]},
                authorization,
            )
            out = []
            for f in (fc or {}).get("features") or []:
                sn = str((f.get("properties") or {}).get("survey_no") or "")
                # survey number 0 is the village's road / public strip: road space
                if f.get("geometry") and sn.split("/")[0].strip() not in ("0", ""):
                    g = shapely.from_geojson(json.dumps(f["geometry"]))
                    out.append(_metric(shapely.make_valid(g)))
            return out

        return self._cached(("v", *key), load)

    def _hobli_outlines(self, key: tuple, authorization: str | None) -> list:
        def load():
            fc = self._cadastral(
                "/boundaries",
                {"dist": key[0], "taluk": key[1], "hobli": key[2]},
                authorization,
            )
            out = []
            for f in (fc or {}).get("features") or []:
                code = str((f.get("properties") or {}).get("village_code") or "")
                if f.get("geometry") and code:
                    g = shapely.from_geojson(json.dumps(f["geometry"]))
                    out.append((code, _metric(shapely.make_valid(g))))
            return out

        return self._cached(("h", *key), load)

    def village_parcels(self, q: dict, authorization: str | None, parcel=None):
        """Survey parcels of the parcel's village and of the villages of its hobli within
        60 m (roads often run on village boundaries), with an STRtree; None when the
        cadastral service cannot be read."""
        hob = (q["dist"], q["taluk"], q["hobli"])
        vlgs = [q["vlg"]]
        if parcel is not None:
            near = parcel.buffer(60.0)
            vlgs += [
                c
                for c, g in self._hobli_outlines(hob, authorization)
                if c != q["vlg"] and g.intersects(near)
            ]
        geoms = []
        for v in vlgs[:4]:
            geoms.extend(self._village_geoms((*hob, v), authorization))
        return (geoms, shapely.STRtree(geoms)) if geoms else None

    @staticmethod
    def gap_estimate(centre, parcel, row_m: float, village) -> dict | None:
        if centre is None or village is None:
            return None
        geoms, tree = village
        stretch = centre.intersection(parcel.buffer(row_m / 2 + 30.0))
        lines = [
            g
            for g in getattr(stretch, "geoms", [stretch])
            if g.geom_type == "LineString" and g.length > 1
        ]
        widths = []
        for ln in lines:
            n = max(1, int(ln.length // STATION_M))
            for k in range(n + 1):
                d = min(ln.length, (k + 0.5) * ln.length / (n + 1))
                p = ln.interpolate(d)
                a, b = (
                    ln.interpolate(max(0.0, d - 2.0)),
                    ln.interpolate(min(ln.length, d + 2.0)),
                )
                tx, ty = b.x - a.x, b.y - a.y
                tl = float(np.hypot(tx, ty))
                if not tl:
                    continue
                nx, ny = -ty / tl, tx / tl
                if len(tree.query(p, predicate="within")):
                    continue  # the plan road runs inside a survey number: no gap to read
                sides = []
                for s in (1, -1):
                    ray = shapely.LineString(
                        [(p.x, p.y), (p.x + s * nx * RAY_M, p.y + s * ny * RAY_M)]
                    )
                    hits = [
                        geoms[int(i)] for i in tree.query(ray, predicate="intersects")
                    ]
                    ds = [p.distance(h.intersection(ray)) for h in hits]
                    if not ds:
                        break
                    sides.append(min(ds))
                if len(sides) == 2:
                    widths.append(sides[0] + sides[1])
        if not widths:
            return None
        w = np.array(widths)
        med = float(np.median(w))
        if med < MIN_GAP_M:
            return None  # the parcels meet across the plan line: no road on the cadastral map
        iqr = [
            round(float(np.percentile(w, 25)), 1),
            round(float(np.percentile(w, 75)), 1),
        ]
        conf = "MEDIUM" if len(w) >= 3 and iqr[1] - iqr[0] <= 3.0 else "LOW"
        return {
            "value_m": round(med, 1),
            "tier": band_of(med),
            "method": "cadastral_gap",
            "confidence": conf,
            "samples": len(w),
            "iqr_m": iqr,
            "note": GAP_NOTE,
        }

    # --- answer ---------------------------------------------------------------------------

    def zr_rows(self, plan_id: str, band: str | None) -> list[dict]:
        z = self.zr.get(plan_id) or {}
        if not band:
            return []
        return [
            {
                "doc_id": z.get("doc_id"),
                **{
                    k: r[k]
                    for k in (
                        "table",
                        "use",
                        "rule",
                        "value",
                        "pdf_page",
                        "printed_page",
                    )
                },
            }
            for r in z.get("rules", [])
            if r.get("band") == band
        ]

    def abutting(
        self, parcel, q: dict, road_width_m: float | None, authorization: str | None
    ) -> list[dict]:
        self.ensure()
        groups: dict[tuple, dict] = {}
        for plan_id, pr in self.plans.items():
            if pr.tree is None:
                continue
            for i in pr.tree.query(parcel, predicate="dwithin", distance=NEAR_M):
                i = int(i)
                p = pr.props[i]
                key = (plan_id, p.get("row_m"), p.get("status"), p.get("road_name"))
                g = groups.setdefault(key, {"plan": pr, "idx": []})
                g["idx"].append(i)
        out = []
        for (plan_id, row_m, status, name), g in groups.items():
            pr = g["plan"]
            corr = shapely.union_all([pr.corridors[i] for i in g["idx"]])
            props = [pr.props[i] for i in g["idx"]]
            best = max(props, key=lambda p: CONF_RANK.get(p.get("confidence"), 0))
            cents = [
                pr.centrelines[i] for i in g["idx"] if pr.centrelines[i] is not None
            ]
            frontage = parcel.boundary.intersection(corr.buffer(NEAR_M)).length
            bands = [
                p["drawn_band_m"] for p in props if p.get("drawn_band_m") is not None
            ]
            meta = pr.meta
            doc_id = meta.get("doc_id") or best.get("doc_id")
            out.append(
                {
                    "plan_id": plan_id,
                    "doc_id": doc_id,
                    "doc_status": (self.docs.get(doc_id) or {}).get("status")
                    or meta.get("doc_status")
                    or "reference",
                    "road_name": name,
                    "row_m": float(row_m),
                    "status": status,
                    "status_text": STATUS_TEXT.get(status, status),
                    "width_source": best.get("width_source"),
                    "plan_confidence": best.get("confidence") or "MEDIUM",
                    "distance_m": round(float(parcel.distance(corr)), 1),
                    "frontage_m": round(float(frontage), 1),
                    "widening_area_sqm": round(
                        float(parcel.intersection(corr).area), 1
                    ),
                    "drawn_band_m": round(float(np.median(bands)), 1)
                    if bands
                    else None,
                    "_bands": len(bands),
                    "_centre": shapely.union_all(cents) if cents else None,
                }
            )
        out.sort(key=lambda r: (-r["frontage_m"], r["distance_m"]))
        out = out[:MAX_ROADS]
        village = self.village_parcels(q, authorization, parcel) if out else None
        for k, r in enumerate(out):
            centre = r.pop("_centre")
            n_bands = r.pop("_bands")
            est = None
            if r["status"] in DRAWN_EXISTING and r["drawn_band_m"] is not None:
                # the grey band on the plan is the existing road, drawn to scale (#78)
                est = {
                    "value_m": r["drawn_band_m"],
                    "tier": band_of(r["drawn_band_m"]),
                    "method": "plan_drawn",
                    # LOW: matches labels on 12-24 m roads but not cadastral road strips (#78)
                    "confidence": "LOW",
                    "samples": n_bands,
                    "iqr_m": None,
                    "note": PLAN_DRAWN_NOTE,
                }
            elif r["status"] != "proposed":
                est = self.gap_estimate(centre, parcel, r["row_m"], village)
            declared = float(road_width_m) if (road_width_m and k == 0) else None
            # only a MEDIUM estimate stands in for the existing width; a LOW one is shown, not used
            usable = est is not None and est["confidence"] == "MEDIUM"
            used = (
                declared
                if declared is not None
                else (est["value_m"] if usable else None)
            )
            band = band_of(used)
            warns = []
            unc = float(
                self.plans[r["plan_id"]].meta.get("placement_uncertainty_m") or 0
            )
            if unc > 10:
                warns.append(
                    f"Road positions on this plan are approximate (about ±{unc:.0f} m): the area inside the ROW is indicative."
                )
            if r["doc_status"] == "reference":
                warns.append(
                    f"Road source {r['doc_id']}: status not stated on its sheets; what the plan draws, not an approved width."
                )
            if r["status"] in ("proposed", "ring_proposed"):
                warns.append(
                    "Proposed road (plan): a warning and a dedication, not today's access."
                )
            if r["widening_area_sqm"] >= 1.0 and r["status"] != "existing_drawn":
                warns.append(
                    f"About {r['widening_area_sqm']:.0f} m² of the parcel lies inside the plan ROW ({r['row_m']:g} m): "
                    "a likely dedication for widening (A_net = A_gross − dedication)."
                )
            if (
                used is not None
                and r["status"] not in ("proposed", "existing_drawn")
                and r["row_m"] > used + 0.5
            ):
                warns.append(
                    f"Plan ROW {r['row_m']:g} m is wider than the existing width {used:g} m: the road is marked for widening."
                )
            if declared is not None and est is not None:
                d = abs(
                    BAND_ORDER.index(band_of(declared)) - BAND_ORDER.index(est["tier"])
                )
                if d > 1:
                    warns.append(
                        f"Declared width {declared:g} m is more than one band from the estimate "
                        f"{est['value_m']:g} m; check on site."
                    )
            if est is not None and est["confidence"] == "LOW":
                warns.append(
                    "Existing-width estimate is LOW confidence and not used; enter the measured width."
                )
            if used is None:
                warns.append(
                    "Existing width unknown: enter the measured width to see the Zonal Regulations rows."
                )
            bl = next(
                (
                    b
                    for b in (self.zr.get(r["plan_id"]) or {}).get("building_lines", [])
                    if b["road"] == r["road_name"]
                ),
                None,
            )
            if bl:
                warns.append(
                    f"Building line {bl['building_line_m']:g} m from the ROW edge (Zonal Regulations Table 21)."
                )
            r.update(
                {
                    "existing_width": est,
                    "declared_width_m": declared,
                    "width_used_m": used,
                    "width_used_source": "declared"
                    if declared is not None
                    else ("estimate" if usable else "none"),
                    "tier": band,
                    "zr_rules": self.zr_rows(r["plan_id"], band),
                    "warnings": warns,
                }
            )
        return out
