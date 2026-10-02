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
import pandas as pd
import shapely
from fastapi import HTTPException
from pyproj import Transformer

from app.services.store import (
    CRS_METRIC,
    CRS_WGS84,
    OVERLAY_KINDS,
    OVERLAY_NOTES,
    SIMPLIFY_LEVELS,
    PlanLayers,
)

MAX_BBOX_DEG = 0.05
DEFAULT_SIMPLIFY_M = 8
TRACE_SHARE = 0.01  # hits below 1 % of the parcel ...
TRACE_AREA_M2 = 20.0  # ... or below 20 m2 are trace hits
COORD_DECIMALS = 6  # ~0.1 m in WGS84
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


def check_simplify(simplify_m: int | None) -> int:
    tol = DEFAULT_SIMPLIFY_M if simplify_m is None else simplify_m
    if tol not in SIMPLIFY_LEVELS:
        raise HTTPException(
            status_code=400,
            detail=f"simplify_m must be one of {list(SIMPLIFY_LEVELS)}",
        )
    return tol


def feature_collection(
    gdf: gpd.GeoDataFrame, geoms: np.ndarray, props: list[str], extra: dict
) -> dict:
    """GeoJSON in WGS84 from metric geometries; coordinates rounded to ~0.1 m."""
    out = gpd.GeoSeries(geoms, crs=CRS_METRIC).to_crs(CRS_WGS84).values
    out = shapely.transform(np.asarray(out), lambda xy: np.round(xy, COORD_DECIMALS))
    feats = []
    for row, geom in zip(gdf[props].to_dict("records"), out, strict=True):
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
        "status_condition": plan.get("status_condition") or None,
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
    "status_condition",
    "cartographic",
    "inferred_note",
    "source_layer",
    "sheet",
    "qa",
]


def _cartographic(row) -> bool:
    """True for drawing-artefact classes (road space); layers built before 1.14 lack it."""
    v = row.get("cartographic", False)
    return False if v is None or pd.isna(v) else bool(v)


def zones_in_bbox(
    layers: PlanLayers, plan: dict, bbox: str, simplify_m: int | None = None
) -> dict:
    tol = check_simplify(simplify_m)
    box = parse_bbox(bbox)
    z = layers.zones
    idx = z.sindex.query(box, predicate="intersects")
    sub = z.iloc[idx].copy()
    sub["inferred_note"] = sub["note"]
    sub["cartographic"] = (
        sub["cartographic"].fillna(False).astype(bool)
        if "cartographic" in sub.columns
        else False
    )
    return feature_collection(
        sub,
        layers.zones_simplified[tol][idx],
        ZONE_PROPS,
        {"plan": plan_ref(plan), "simplify_m": tol},
    )


def overlays_in_bbox(
    layers: PlanLayers,
    plan: dict,
    bbox: str,
    kind: str,
    simplify_m: int | None = None,
) -> dict:
    if kind not in OVERLAY_KINDS:
        raise HTTPException(
            status_code=400, detail=f"kind must be one of {sorted(OVERLAY_KINDS)}"
        )
    tol = check_simplify(simplify_m)
    box = parse_bbox(bbox)
    o = layers.overlays
    idx = o.sindex.query(box, predicate="intersects")
    idx = idx[o["class_norm"].to_numpy()[idx] == OVERLAY_KINDS[kind]]
    sub = o.iloc[idx].copy()
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
        "status_condition",
        "note",
        "method",
        "qa",
    ]
    return feature_collection(
        sub,
        layers.overlays_simplified[tol][idx],
        props,
        {"plan": plan_ref(plan), "kind": kind, "simplify_m": tol},
    )


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
    return _polygonal(shapely.make_valid(g)), fc["features"][0].get("properties", {})


def _polygonal(g: shapely.Geometry) -> shapely.Geometry:
    """make_valid can return a GeometryCollection with stray lines/points (a spike in the
    source ring); its boundary is None. Keep only the polygon parts."""
    parts = [
        p
        for p in shapely.get_parts(g)
        if p.geom_type in ("Polygon", "MultiPolygon") and not p.is_empty
    ]
    return shapely.union_all(parts) if parts else shapely.Polygon()


# coarser = higher; position_uncertainty_m of a hit spanning layers comes from the coarser
LAYER_RANK = {"detail": 0, "hobli": 1, "lpa_map": 2, "composite": 3}


def position_uncertainty(qa: dict) -> float:
    rmse = qa.get("georef_rmse_m") or 0.0
    mpx = qa.get("m_per_px") or 0.0
    return math.sqrt(rmse**2 + mpx**2)


def zone_hits(
    layers: PlanLayers, parcel: shapely.Geometry
) -> tuple[list[dict], list[dict]]:
    """(hits, trace_hits). Trace hits (< 1 % of the parcel or < 20 m2) do not count for the
    other hits' edges: trace-zone polygons near the parcel are treated as part of each other
    zone. A trace hit keeps its own edge_distance_m / near_edge.

    edge_distance_m is the distance from the parcel boundary to the zone's boundary, 0 when
    they cross. Zones form a coverage, so only the zone pieces touching the parcel are unioned
    (a union over the whole 200 m window was slow once road space added many small pieces):
    a parcel inside the zone measures to the nearest other zone or the plan's outer boundary,
    capped at EDGE_WINDOW_M; zone pieces wholly inside the parcel measure to those pieces."""
    z = layers.zones
    touching = z.iloc[z.sindex.query(parcel, predicate="intersects")]
    if touching.empty:
        return [], []
    window = z.iloc[
        z.sindex.query(parcel.buffer(EDGE_WINDOW_M), predicate="intersects")
    ]
    area = parcel.area
    groups = {}
    for label, grp in touching.groupby("zone_label_native", sort=False):
        inter = [parcel.intersection(g) for g in grp.geometry]
        ov_area = sum(i.area for i in inter)
        if ov_area > 0:
            groups[label] = (grp, inter, ov_area)
    trace = {
        lb
        for lb, (_, _, a) in groups.items()
        if a < TRACE_SHARE * area or a < TRACE_AREA_M2
    }
    w_labels = window["zone_label_native"].to_numpy()
    w_geoms = np.asarray(window.geometry.values)
    w_trace = np.isin(w_labels, list(trace))
    outer = layers.outer_boundary
    clip_box = shapely.box(*parcel.buffer(2.0).bounds)
    hits, traces = [], []
    for label, (grp, inter, ov_area) in groups.items():
        inferred_area = sum(
            i.area for i, n in zip(inter, grp["note"], strict=True) if n
        )
        mine = w_labels == label
        if label not in trace:
            mine |= w_trace
        # the zone (with trace zones merged in) is only unioned where it touches the parcel;
        # anything across that local outline on the parcel boundary is another zone
        local = w_geoms[mine & shapely.intersects(w_geoms, parcel)]
        # clip to just beyond the parcel first: big zone polygons made the union slow, and
        # edges the clip adds lie outside the parcel boundary
        local_u = shapely.union_all(shapely.intersection(local, clip_box))
        rim = parcel.boundary
        if rim.intersects(local_u.boundary) or (
            outer is not None and rim.intersects(outer)
        ):
            edge = 0.0
        elif local_u.covers(rim):
            # parcel inside the zone: the edge is where another zone (or the LPA edge) starts
            og = w_geoms[~mine]
            ds = list(shapely.distance(og, parcel)) if len(og) else []
            if outer is not None:
                ds.append(parcel.distance(outer))
            edge = float(min([EDGE_WINDOW_M, *ds]))  # numpy floats break JSON bools
        else:
            # zone pieces lie wholly inside the parcel
            edge = float(rim.distance(local_u))
        layers_r = [plain(x) or "composite" for x in grp["source_layer"]]
        sheets_r = [plain(x) for x in grp["sheet"]]
        qas = []
        for q, lay, sh in zip(grp["qa"], layers_r, sheets_r, strict=True):
            q = dict(plain(q))
            q.setdefault("source_layer", lay)
            q["source_layer"] = q["source_layer"] or lay
            q.setdefault("sheet", sh)
            q["sheet"] = q["sheet"] or sh
            q["warnings"] = list(q.get("warnings") or [])  # 1.17; [] for older layers
            qas.append(q)
        share = {}
        for i, lay, sh in zip(inter, layers_r, sheets_r, strict=True):
            share[(lay, sh)] = share.get((lay, sh), 0.0) + i.area
        top_layer, top_sheet = max(share, key=share.get)
        layer_set = {lay for lay, _ in share}
        # several layers: the coarser layer's uncertainty, never the largest-share sheet's
        coarse = max(layer_set, key=lambda lay: LAYER_RANK.get(lay, 9))
        unc = max(
            position_uncertainty(q)
            for q, lay in zip(qas, layers_r, strict=True)
            if lay == coarse
        )
        first = grp.iloc[0]
        hit = {
            "plan_id": first["plan_id"],
            "zone_label_native": label,
            "zone_code_native": plain(first["zone_code_native"]),
            "class_norm": plain(first["class_norm"]),
            "cartographic": _cartographic(first),
            "status": first["status"],
            "status_label": first["status_label"],
            "status_condition": plain(first.get("status_condition")) or None,
            "zone_uids": list(grp["zone_uid"]),
            "sheet_doc_ids": sorted(set(grp["doc_id"])),
            "overlap_pct": round(100 * ov_area / area, 2),
            "edge_distance_m": round(edge, 1),
            "position_uncertainty_m": round(unc, 1),
            "near_edge": edge < unc,
            "inferred": inferred_area > 0,
            "inferred_share_pct": round(100 * inferred_area / ov_area, 1),
            "inferred_notes": sorted({n for n in grp["note"] if n}),
            "sheets_qa": list({(q["doc_id"], q.get("sheet")): q for q in qas}.values()),
            "source_layer": top_layer,
            "sheet": top_sheet,
            "mixed_source_layers": len(layer_set) > 1,
        }
        (traces if label in trace else hits).append(hit)
    hits.sort(key=lambda h: -h["overlap_pct"])
    traces.sort(key=lambda h: -h["overlap_pct"])
    return hits, traces


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
                        "status_condition": plain(row.get("status_condition")) or None,
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
                    "status_condition": plain(row.get("status_condition")) or None,
                    "note": OVERLAY_NOTES["stream_centreline"],
                    "distance_m": round(d, 1),
                }
    out["nearest_stream_centreline"] = best
    return out
