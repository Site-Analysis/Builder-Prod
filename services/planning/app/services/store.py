# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

"""In-memory planning data: source register, plan registry, zone and overlay layers.

Data sources (env):
  PLANNING_REGISTER_DIR  plans.csv + plan_docs.csv (repo: infra/planning)
  PLANNING_DATA_DIR      <plan_id>.parquet (zones) and <plan_id>_overlays.parquet
                         (GeoParquet, EPSG:32643), e.g. <data-root>/planning/zones

Layers are stored in EPSG:32643 with an STRtree each (geopandas sindex) and served as
WGS84. Status and status_label on every zone/overlay come from the register row of its
doc_id, never from the layer file.
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
    zones_simplified: dict[int, np.ndarray] = field(default_factory=dict)
    overlays_simplified: dict[int, np.ndarray] = field(default_factory=dict)
    # the plan's outer boundary (its LPA) as a line; a zone edge for edge distances
    outer_boundary: shapely.Geometry | None = None


@dataclass
class Store:
    plans: dict[str, dict] = field(default_factory=dict)
    docs: dict[str, dict] = field(default_factory=dict)
    layers: dict[str, PlanLayers] = field(default_factory=dict)
    # village key (dist, taluk, hobli, vlg) -> authority_villages.csv row
    authority: dict[tuple[str, str, str, str], dict] = field(default_factory=dict)
    authority_dists: set[str] = field(default_factory=set)
    # LPA boundary per authority (EPSG:32643), e.g. {"BDA": polygon}
    lpa: dict[str, shapely.Geometry] = field(default_factory=dict)


def _read_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        log.warning("register file missing: %s", path)
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _apply_register_status(
    gdf: gpd.GeoDataFrame, docs: dict[str, dict]
) -> gpd.GeoDataFrame:
    """Status comes from the source register row of each feature's doc_id."""
    gdf = gdf.copy()
    gdf["status"] = [
        docs.get(d, {}).get("status", s)
        for d, s in zip(gdf["doc_id"], gdf["status"], strict=True)
    ]
    gdf["status_label"] = [
        docs.get(d, {}).get("status_label", s)
        for d, s in zip(gdf["doc_id"], gdf["status_label"], strict=True)
    ]
    gdf["status_condition"] = [
        docs.get(d, {}).get("status_condition") or None for d in gdf["doc_id"]
    ]
    return gdf


def _load_layer(path: str, docs: dict[str, dict]) -> gpd.GeoDataFrame | None:
    if not os.path.exists(path):
        return None
    gdf = gpd.read_parquet(path)
    if gdf.crs is None or gdf.crs.to_epsg() != CRS_METRIC:
        gdf = gdf.to_crs(CRS_METRIC)
    gdf = _apply_register_status(gdf, docs)
    _ = gdf.sindex  # build the STRtree now, not on the first request
    return gdf


def simplify_levels(
    gdf: gpd.GeoDataFrame, group: str | None = None
) -> dict[int, np.ndarray]:
    """Pre-simplified geometry per tolerance. Polygons that form a coverage (per group)
    are simplified together so shared edges stay shared; lines are simplified singly."""
    out = {}
    geoms = np.asarray(gdf.geometry.values)
    if group is None:
        groups = [np.arange(len(gdf))]
    else:
        col = gdf[group].to_numpy()
        groups = [np.flatnonzero(col == v) for v in np.unique(col)]
    for tol in SIMPLIFY_LEVELS:
        res = geoms.copy()
        for idx in groups:
            part = geoms[idx]
            if np.isin(shapely.get_type_id(part), (3, 6)).all():
                res[idx] = shapely.coverage_simplify(part, tol)
            else:
                res[idx] = shapely.simplify(part, tol, preserve_topology=True)
        out[tol] = res
    return out


def load_store() -> Store:
    reg = os.getenv("PLANNING_REGISTER_DIR", "infra/planning")
    data = os.getenv("PLANNING_DATA_DIR", "data/planning/zones")
    st = Store()
    st.plans = {r["plan_id"]: r for r in _read_csv(os.path.join(reg, "plans.csv"))}
    st.docs = {r["doc_id"]: r for r in _read_csv(os.path.join(reg, "plan_docs.csv"))}
    for plan_id in st.plans:
        zones = _load_layer(os.path.join(data, f"{plan_id}.parquet"), st.docs)
        overlays = _load_layer(
            os.path.join(data, f"{plan_id}_overlays.parquet"), st.docs
        )
        if zones is not None or overlays is not None:
            st.layers[plan_id] = PlanLayers(
                plan_id,
                zones,
                overlays,
                simplify_levels(zones) if zones is not None else {},
                simplify_levels(overlays, "class_norm") if overlays is not None else {},
            )
            log.info(
                "loaded %s: %s zones, %s overlays",
                plan_id,
                0 if zones is None else len(zones),
                0 if overlays is None else len(overlays),
            )
    auth_csv = os.getenv(
        "PLANNING_AUTHORITY_CSV", os.path.join(reg, "authority_villages.csv")
    )
    for r in _read_csv(auth_csv):
        st.authority[(r["dist"], r["taluk"], r["hobli"], r["vlg"])] = r
        st.authority_dists.add(r["dist"])
    lpa_path = os.path.join(data, "BDA-RMP2031_lpa.parquet")
    if os.path.exists(lpa_path):
        lpa = gpd.read_parquet(lpa_path).to_crs(CRS_METRIC)
        st.lpa["BDA"] = shapely.union_all(list(lpa.geometry))
        shapely.prepare(st.lpa["BDA"])
        if "BDA-RMP2031" in st.layers:
            st.layers["BDA-RMP2031"].outer_boundary = st.lpa["BDA"].boundary
    return st


@lru_cache(maxsize=1)
def get_store() -> Store:
    return load_store()
