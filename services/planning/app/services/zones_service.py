# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Zone / overlay queries in EPSG:32643, answers in WGS84 GeoJSON."""

from __future__ import annotations

import json
import math
import os

import geopandas as gpd
import httpx
import numpy as np
import shapely
from fastapi import HTTPException
from pyproj import Transformer

from app.services.store import (
    CRS_METRIC,
    CRS_WGS84,
    OVERLAY_KINDS,
    OVERLAY_NOTES,
    PlanLayers,
)

MAX_BBOX_DEG = 0.05
STREAM_NEARBY_M = 100.0
EDGE_WINDOW_M = 200.0  # same-label zones within this of the parcel are merged before measuring edges
CADASTRAL_URL = os.getenv("CADASTRAL_URL", "http://localhost:8011")
_to_metric = Transformer.from_crs(CRS_WGS84, CRS_METRIC, always_xy=True)


def plain(v):
    """numpy / pyarrow values -> JSON-safe Python values."""
    if isinstance(v, dict):
        return {k: plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, np.ndarray)):
        return [plain(x) for x in v]
    if isinstance(v, np.generic):
        return v.item()
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def parse_bbox(bbox: str) -> shapely.Polygon:
    try:
        x0, y0, x1, y1 = (float(v) for v in bbox.split(","))
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="bbox must be minLng,minLat,maxLng,maxLat"
        ) from exc
    if x1 <= x0 or y1 <= y0:
        raise HTTPException(status_code=400, detail="bbox min must be below max")
    if x1 - x0 > MAX_BBOX_DEG + 1e-9 or y1 - y0 > MAX_BBOX_DEG + 1e-9:
        raise HTTPException(
            status_code=400, detail=f"bbox larger than {MAX_BBOX_DEG} degrees per side"
        )
    return shapely.transform(
        shapely.box(x0, y0, x1, y1),
        lambda xy: np.column_stack(_to_metric.transform(xy[:, 0], xy[:, 1])),
    )


def feature_collection(gdf: gpd.GeoDataFrame, props: list[str], extra: dict) -> dict:
    out = gdf.to_crs(CRS_WGS84)
    feats = []
    for row, geom in zip(out[props].to_dict("records"), out.geometry, strict=True):
        feats.append(
            {
                "type": "Feature",
                "geometry": json.loads(shapely.to_geojson(geom)),
                "properties": plain(row),
            }
        )
    return {"type": "FeatureCollection", **extra, "features": feats}


def plan_ref(plan: dict) -> dict:
    return {
        "plan_id": plan["plan_id"],
        "status": plan["status"],
        "status_label": plan["status_label"],
        "go_ref": plan.get("go_ref") or None,
        "coverage": "full",
    }


ZONE_PROPS = [
    "zone_uid",
    "plan_id",
    "doc_id",
    "zone_label_native",
    "zone_code_native",
    "class_norm",
    "status",
    "status_label",
    "inferred_note",
    "qa",
]


def zones_in_bbox(layers: PlanLayers, plan: dict, bbox: str) -> dict:
    box = parse_bbox(bbox)
    z = layers.zones
    idx = z.sindex.query(box, predicate="intersects")
    sub = z.iloc[idx].copy()
    sub["inferred_note"] = sub["note"]
    return feature_collection(sub, ZONE_PROPS, {"plan": plan_ref(plan)})


def overlays_in_bbox(layers: PlanLayers, plan: dict, bbox: str, kind: str) -> dict:
    if kind not in OVERLAY_KINDS:
        raise HTTPException(
            status_code=400, detail=f"kind must be one of {sorted(OVERLAY_KINDS)}"
        )
    box = parse_bbox(bbox)
    o = layers.overlays
    o = o[o["class_norm"] == OVERLAY_KINDS[kind]]
    sub = o.iloc[o.sindex.query(box, predicate="intersects")].copy()
    sub["kind"] = kind
    sub["note"] = OVERLAY_NOTES[kind]
    props = [
        "overlay_uid",
        "plan_id",
        "doc_id",
        "kind",
        "overlay_label_native",
        "status",
        "status_label",
        "note",
        "method",
        "qa",
    ]
    return feature_collection(sub, props, {"plan": plan_ref(plan), "kind": kind})


# ---------------------------------------------------------------- /zones/at
async def fetch_parcel(
    dist: str, taluk: str, hobli: str, vlg: str, survey: str, authorization: str | None
) -> dict:
    """Parcel GeoJSON (WGS84) from the cadastral service; the caller's token is forwarded."""
    params = {
        "dist": dist,
        "taluk": taluk,
        "hobli": hobli,
        "vlg": vlg,
        "survey": survey,
    }
    headers = {"Authorization": authorization} if authorization else {}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get(
                f"{CADASTRAL_URL}/data", params=params, headers=headers
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"Cadastral service unavailable: {exc}"
        ) from exc
    if r.status_code != 200:
        raise HTTPException(
            status_code=502, detail=f"Cadastral service returned {r.status_code}"
        )
    fc = r.json()
    if not fc.get("features"):
        raise HTTPException(status_code=404, detail="Parcel not found")
    return fc


def parcel_geometry(fc: dict) -> tuple[shapely.Geometry, dict]:
    """Union of the parcel features in EPSG:32643; cadastral geometry can be invalid,
    so each part is repaired before the union."""
    geoms = [
        shapely.make_valid(shapely.from_geojson(json.dumps(f["geometry"])))
        for f in fc["features"]
        if f.get("geometry")
    ]
    g = shapely.union_all(geoms)
    g = shapely.transform(
        g, lambda xy: np.column_stack(_to_metric.transform(xy[:, 0], xy[:, 1]))
    )
    return shapely.make_valid(g), fc["features"][0].get("properties", {})


def position_uncertainty(qa: dict) -> float:
    rmse = qa.get("georef_rmse_m") or 0.0
    mpx = qa.get("m_per_px") or 0.0
    return math.sqrt(rmse**2 + mpx**2)


def zone_hits(layers: PlanLayers, parcel: shapely.Geometry) -> list[dict]:
    z = layers.zones
    touching = z.iloc[z.sindex.query(parcel, predicate="intersects")]
    if touching.empty:
        return []
    window = z.iloc[
        z.sindex.query(parcel.buffer(EDGE_WINDOW_M), predicate="intersects")
    ]
    area = parcel.area
    hits = []
    for label, grp in touching.groupby("zone_label_native", sort=False):
        inter = [parcel.intersection(g) for g in grp.geometry]
        ov_area = sum(i.area for i in inter)
        if ov_area <= 0:
            continue
        inferred_area = sum(
            i.area for i, n in zip(inter, grp["note"], strict=True) if n
        )
        zone_union = shapely.union_all(
            list(window[window["zone_label_native"] == label].geometry)
        )
        edge = (
            0.0
            if parcel.boundary.intersects(zone_union.boundary)
            else parcel.boundary.distance(zone_union.boundary)
        )
        qas = [plain(q) for q in grp["qa"]]
        unc = max(position_uncertainty(q) for q in qas)
        first = grp.iloc[0]
        hits.append(
            {
                "plan_id": first["plan_id"],
                "zone_label_native": label,
                "zone_code_native": plain(first["zone_code_native"]),
                "class_norm": plain(first["class_norm"]),
                "status": first["status"],
                "status_label": first["status_label"],
                "zone_uids": list(grp["zone_uid"]),
                "sheet_doc_ids": sorted(set(grp["doc_id"])),
                "overlap_pct": round(100 * ov_area / area, 2),
                "edge_distance_m": round(edge, 1),
                "position_uncertainty_m": round(unc, 1),
                "near_edge": edge < unc,
                "inferred": inferred_area > 0,
                "inferred_share_pct": round(100 * inferred_area / ov_area, 1),
                "inferred_notes": sorted({n for n in grp["note"] if n}),
                "sheets_qa": list({q["doc_id"]: q for q in qas}.values()),
            }
        )
    return sorted(hits, key=lambda h: -h["overlap_pct"])


def overlays_nearby(layers_list: list[PlanLayers], parcel: shapely.Geometry) -> dict:
    out = {"ngt_buffer": [], "forest_symbol": [], "nearest_stream_centreline": None}
    area = parcel.area
    best = None
    for layers in layers_list:
        o = layers.overlays
        if o is None:
            continue
        for kind in ("ngt_buffer", "forest_symbol"):
            sub = o[o["class_norm"] == OVERLAY_KINDS[kind]]
            for _, row in sub.iloc[
                sub.sindex.query(parcel, predicate="intersects")
            ].iterrows():
                out[kind].append(
                    {
                        "overlay_uid": row["overlay_uid"],
                        "plan_id": row["plan_id"],
                        "kind": kind,
                        "status": row["status"],
                        "status_label": row["status_label"],
                        "note": OVERLAY_NOTES[kind],
                        "overlap_pct": round(
                            100 * parcel.intersection(row.geometry).area / area, 2
                        ),
                    }
                )
        st = o[o["class_norm"] == OVERLAY_KINDS["stream_centreline"]]
        for _, row in st.iloc[
            st.sindex.query(parcel, predicate="dwithin", distance=STREAM_NEARBY_M)
        ].iterrows():
            d = parcel.distance(row.geometry)
            if best is None or d < best["distance_m"]:
                best = {
                    "overlay_uid": row["overlay_uid"],
                    "plan_id": row["plan_id"],
                    "status": row["status"],
                    "status_label": row["status_label"],
                    "note": OVERLAY_NOTES["stream_centreline"],
                    "distance_m": round(d, 1),
                }
    out["nearest_stream_centreline"] = best
    return out
