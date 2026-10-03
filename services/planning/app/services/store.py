# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

"""In-memory planning data: source register, plan registry, and the on-demand zone layers.

Data sources (env):
  PLANNING_REGISTER_DIR  plans.csv, plan_docs.csv, authority_villages.csv, authorities.csv,
                         sources_checked.csv and layer_index.json (repo: infra/planning)

Zones, overlays and LPA outlines are not read from disk: the layer index names each sheet's
source, and app.services.ondemand downloads, extracts and caches them in RAM when a query
needs them (contract 1.18). Status and status_label on every zone/overlay come from the
register row of its doc_id, never from the extraction.
"""

from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache

import geopandas as gpd
import numpy as np
import shapely

log = logging.getLogger(__name__)

CRS_METRIC = 32643
SIMPLIFY_LEVELS = (2, 8, 25)  # metres; must match SimplifyM in contracts/planning.yaml
CRS_WGS84 = 4326
OVERLAY_KINDS = {
    "ngt_buffer": "ngt_buffer",
    "forest_symbol": "forest_symbol_area",
    "stream_centreline": "stream_centreline",
}
OVERLAY_NOTES = {
    "ngt_buffer": "Map symbol (NGT Buffer hatch), not a measured buffer",
    "forest_symbol": "Map symbol (forest tree glyphs), not a measured forest boundary",
    "stream_centreline": "Map symbol (stream centreline), not a surveyed stream",
}


@dataclass
class PlanLayers:
    plan_id: str
    zones: gpd.GeoDataFrame | None = None
    overlays: gpd.GeoDataFrame | None = None
    # tolerance (m) -> geometry array aligned with the layer rows (map display only)
    zones_simplified: dict[int, np.ndarray] = field(default_factory=dict)  # lazy
    overlays_simplified: dict[int, np.ndarray] = field(default_factory=dict)  # lazy
    # the plan's outer boundary (its LPA) as a line; a zone edge for edge distances
    outer_boundary: shapely.Geometry | None = None


@dataclass
class Store:
    plans: dict[str, dict] = field(default_factory=dict)
    docs: dict[str, dict] = field(default_factory=dict)
    # village key (dist, taluk, hobli, vlg) -> authority_villages.csv row
    authority: dict[tuple[str, str, str, str], dict] = field(default_factory=dict)
    authority_dists: set[str] = field(default_factory=set)
    # authorities.csv rows (authority -> lpa_label, plan_ids, note); sources_checked.csv
    authorities: dict[str, dict] = field(default_factory=dict)
    sources_checked: list[dict] = field(default_factory=list)
    od: object | None = None  # app.services.ondemand.OnDemand
    villages: object | None = (
        None  # app.services.villages.VillageIndex (point -> village)
    )

    def lpas(self, *a, **kw):
        from app.services.ondemand import COMPUTE

        with COMPUTE:
            return self._lpas(*a, **kw)

    def _lpas(
        self,
    ) -> tuple[dict[str, shapely.Geometry], dict[str, shapely.Geometry]] | None:
        """(plan LPAs by authority, BMRDA LPA-map current extents by authority), or None while
        an outline is still loading."""
        od = self.od
        if od is None:
            return {}, {}
        lpa, lpa_map = {}, {}
        for plan_id in list(od.outline_rows):
            outs = od.outlines(plan_id)
            if outs is None:
                return None
            for m, g in outs:
                a = m.get("authority")
                if m.get("extent") == "plan":
                    a = a or (self.plans.get(plan_id) or {}).get("authority")
                    if a and a not in lpa:
                        lpa[a] = g
                elif m.get("extent") == "current" and a and a not in lpa_map:
                    lpa_map[a] = shapely.make_valid(g)
        return lpa, lpa_map

    def layers_for(self, plan_id: str, box: shapely.Geometry):
        from app.services.ondemand import COMPUTE

        self.od.premerge(plan_id, box)  # merge work runs in the worker, outside COMPUTE
        with COMPUTE:
            return self._layers_for(plan_id, box)

    def _layers_for(self, plan_id: str, box: shapely.Geometry):
        """PlanLayers of one plan for a query window (merged zones + overlays), and the pending
        sheets. layers is None while a needed sheet is not ready."""
        from app.services.ondemand import memtrace

        with memtrace(f"merged_zones {plan_id}"):
            zones, pend = self.od.merged_zones(plan_id, box)
        if zones is None:
            return None, pend
        ov, pend_o = self.od.overlays(plan_id, box)
        if ov is None:
            return None, pend_o
        lpa = self.od.plan_lpa(plan_id)
        zones = zones.set_geometry("geometry")
        _ = zones.sindex
        if len(ov):
            _ = ov.sindex
        lay = PlanLayers(
            plan_id,
            zones,
            ov if len(ov) else None,
            simplify_levels(zones) if len(zones) else {},
            simplify_levels(ov, "class_norm") if len(ov) else {},
        )
        if lpa is not None:
            lay.outer_boundary = lpa.boundary
        return lay, []


def _read_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        log.warning("register file missing: %s", path)
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _simplify_one(geoms: np.ndarray, groups: list[np.ndarray], tol: int) -> np.ndarray:
    res = geoms.copy()
    for idx in groups:
        part = geoms[idx]
        if np.isin(shapely.get_type_id(part), (3, 6)).all():
            res[idx] = shapely.coverage_simplify(part, tol)
        else:
            res[idx] = shapely.simplify(part, tol, preserve_topology=True)
    return res


class SimplifyLevels(dict):
    """Simplified geometry per tolerance, computed on first use and cached (map display
    only). Polygons that form a coverage (per group) are simplified together so shared
    edges stay shared; lines are simplified singly. Lazy because precomputing every level
    for every plan (~2.5 M zones with the LPA plans) needed more memory than the host has."""

    def __init__(self, gdf: gpd.GeoDataFrame, group: str | None = None):
        super().__init__()
        self._geoms = np.asarray(gdf.geometry.values)
        if group is None:
            self._groups = [np.arange(len(gdf))]
        else:
            col = gdf[group].to_numpy()
            self._groups = [np.flatnonzero(col == v) for v in np.unique(col)]

    def __missing__(self, tol: int) -> np.ndarray:
        if tol not in SIMPLIFY_LEVELS:
            raise KeyError(tol)
        res = _simplify_one(self._geoms, self._groups, tol)
        self[tol] = res
        return res


def simplify_levels(gdf: gpd.GeoDataFrame, group: str | None = None) -> SimplifyLevels:
    return SimplifyLevels(gdf, group)


def load_store() -> Store:
    from app.services.ondemand import OnDemand

    reg = os.getenv("PLANNING_REGISTER_DIR", "infra/planning")
    st = Store()
    st.plans = {r["plan_id"]: r for r in _read_csv(os.path.join(reg, "plans.csv"))}
    st.docs = {r["doc_id"]: r for r in _read_csv(os.path.join(reg, "plan_docs.csv"))}
    auth_csv = os.getenv(
        "PLANNING_AUTHORITY_CSV", os.path.join(reg, "authority_villages.csv")
    )
    for r in _read_csv(auth_csv):
        st.authority[(r["dist"], r["taluk"], r["hobli"], r["vlg"])] = r
        st.authority_dists.add(r["dist"])
    st.authorities = {
        r["authority"]: r for r in _read_csv(os.path.join(reg, "authorities.csv"))
    }
    st.sources_checked = _read_csv(os.path.join(reg, "sources_checked.csv"))
    from app.services.villages import VillageIndex

    st.villages = VillageIndex(
        [k[:3] for k in st.authority],
        os.getenv("CADASTRAL_URL", "http://localhost:8011"),
    )
    index = os.getenv("PLANNING_LAYER_INDEX", os.path.join(reg, "layer_index.json"))
    if os.path.exists(index):
        st.od = OnDemand(index, st.docs, st.plans)
        st.od.ensure_outlines()  # LPA outlines load in the background at start
    else:
        log.warning("layer index missing: %s (no zone layers)", index)
    return st


@lru_cache(maxsize=1)
def get_store() -> Store:
    return load_store()
