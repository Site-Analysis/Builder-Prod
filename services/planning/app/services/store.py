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

log = logging.getLogger(__name__)

CRS_METRIC = 32643
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


@dataclass
class Store:
    plans: dict[str, dict] = field(default_factory=dict)
    docs: dict[str, dict] = field(default_factory=dict)
    layers: dict[str, PlanLayers] = field(default_factory=dict)


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
            st.layers[plan_id] = PlanLayers(plan_id, zones, overlays)
            log.info(
                "loaded %s: %s zones, %s overlays",
                plan_id,
                0 if zones is None else len(zones),
                0 if overlays is None else len(overlays),
            )
    return st


@lru_cache(maxsize=1)
def get_store() -> Store:
    return load_store()
