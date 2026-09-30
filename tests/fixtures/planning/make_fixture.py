# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Build the tiny synthetic planning fixture used by tests/planning_smoke.py.

Not real plan data: a few squares in EPSG:32643 near Bengaluru with the same columns as
the extracted layers. Run: python tests/fixtures/planning/make_fixture.py
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString, box

HERE = Path(__file__).resolve().parent
QA = {
    "doc_id": "BDA-RMP2031-PLUCOMP",
    "status": "draft",
    "extraction": "raster_palette",
    "georef_rmse_m": 10.1,
    "m_per_px": 4.86,
    "georef_method": "affine_icp_osm_roads",
    "legend_check": "warn",
    "qa_failures": ["fixture"],
    "sheet_scale": "fixture",
}
E, N = 780000, 1435000


def zone(uid, label, cls, geom, note=None):
    return {
        "zone_uid": f"BDA-RMP2031-PLUCOMP-{uid:06d}",
        "plan_id": "BDA-RMP2031",
        "doc_id": "BDA-RMP2031-PLUCOMP",
        "zone_label_native": label,
        "zone_code_native": None,
        "class_norm": cls,
        "status": "draft",
        "status_label": "Draft, never approved",
        "inferred_under_hatch": note == "zone inferred under hatch",
        "inferred_under_stream": note == "zone inferred under stream symbol",
        "note": note,
        "area_m2": geom.area,
        "qa": QA,
        "geometry": geom,
    }


def overlay(uid, label, cls, kind, geom):
    return {
        "overlay_uid": f"BDA-RMP2031-{cls.upper()}-{uid:06d}",
        "plan_id": "BDA-RMP2031",
        "doc_id": "BDA-RMP2031-PLUCOMP",
        "overlay_label_native": label,
        "class_norm": cls,
        "overlay_type": kind,
        "status": "draft",
        "status_label": "Draft, never approved",
        "method": "fixture",
        "size": geom.area if kind == "area" else geom.length,
        "qa": QA,
        "geometry": geom,
    }


zones = gpd.GeoDataFrame(
    [
        zone(1, "Residential", "residential", box(E, N, E + 1000, N + 1000)),
        zone(2, "Commercial", "commercial", box(E + 1000, N, E + 2000, N + 1000)),
        zone(3, "Residential", "residential", box(E, N - 500, E + 1000, N), "zone inferred under hatch"),
        zone(4, "Not coloured on the plan", "uncoloured", box(E + 2000, N, E + 3000, N + 1000)),
    ],
    crs=32643,
)
overlays = gpd.GeoDataFrame(
    [
        overlay(1, "NGT Buffer", "ngt_buffer", "area", box(E - 100, N - 600, E + 1100, N + 50)),
        overlay(2, "Forest", "forest_symbol_area", "area", box(E + 5000, N + 5000, E + 6000, N + 6000)),
        overlay(3, "Streams", "stream_centreline", "line", LineString([(E + 950, N - 100), (E + 950, N + 900)])),
    ],
    crs=32643,
)
zones.to_parquet(HERE / "BDA-RMP2031.parquet")
overlays.to_parquet(HERE / "BDA-RMP2031_overlays.parquet")
print("wrote", HERE)
