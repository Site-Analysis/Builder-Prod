# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Zone and overlay layers, and zones touching a parcel."""

from __future__ import annotations

import json
import os

from fastapi import APIRouter, Header, HTTPException, Query, Response
from fastapi.concurrency import run_in_threadpool

from app.services import zones_service as zs
from app.services.ondemand import COMPUTE, memtrace
from app.services.store import PlanLayers, get_store, simplify_levels

_LAYERS_FLAG = "feature.planning.layers"
# /zones/at looks at zones within this distance of the parcel (edge distances use 200 m)
WINDOW_M = 260.0
NOTE_PENDING = (
    "Some plan sheets are still loading (see pending_sheets); their zones are not in this "
    "answer yet"
)

router = APIRouter(tags=["zones"])


def _flags() -> set[str]:
    return set(os.getenv("FLAGS", "").split())


def _require_flag() -> None:
    if _LAYERS_FLAG not in _flags():
        raise HTTPException(
            status_code=403, detail=f"Feature flag disabled: {_LAYERS_FLAG}"
        )


def _plan_enabled(plan_id: str) -> bool:
    return f"feature.planning.plan.{plan_id}" in _flags()


def _plan(plan_id: str) -> dict:
    st = get_store()
    plan = st.plans.get(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"Unknown plan_id: {plan_id}")
    if not _plan_enabled(plan_id):
        raise HTTPException(
            status_code=403,
            detail=f"Feature flag disabled: feature.planning.plan.{plan_id}",
        )
    if st.od is None or plan_id not in st.od.indexed_plans():
        raise HTTPException(
            status_code=404, detail=f"No zone layer loaded for {plan_id}"
        )
    return plan


def _dedupe(pend: list[dict]) -> list[dict]:
    return list({(p["plan_id"], p["doc_id"], p["sheet"]): p for p in pend}.values())


@router.get("/zones")
def get_zones(
    plan_id: str = Query(...),
    bbox: str = Query(...),
    simplify_m: int | None = Query(None),
) -> Response:
    _require_flag()
    plan = _plan(plan_id)
    st = get_store()
    tol = zs.check_simplify(simplify_m)
    box = zs.parse_bbox(bbox, zs.max_bbox_deg(tol))
    st.od.premerge(plan_id, box)  # merge work runs in the worker, outside COMPUTE
    with COMPUTE, memtrace(f"/zones {plan_id} {bbox}"):
        feats, pend = st.od.zone_features_json(plan_id, box, tol)
    head = {
        "type": "FeatureCollection",
        "plan": zs.plan_ref(plan),
        "simplify_m": tol,
        "build_id": st.od.build_id,
        "pending_sheets": _dedupe(pend),
    }
    # features are cached per chunk as GeoJSON text; the collection is joined, not re-encoded
    body = json.dumps(head)[:-1] + ',"features":[' + ",".join(feats or []) + "]}"
    return Response(content=body, media_type="application/json")


@router.get("/overlays")
def get_overlays(
    plan_id: str = Query(...),
    bbox: str = Query(...),
    kind: str = Query(...),
    simplify_m: int | None = Query(None),
) -> dict:
    _require_flag()
    plan = _plan(plan_id)
    st = get_store()
    if kind not in zs.OVERLAY_KINDS:
        raise HTTPException(
            status_code=400, detail=f"kind must be one of {sorted(zs.OVERLAY_KINDS)}"
        )
    tol = zs.check_simplify(simplify_m)
    box = zs.parse_bbox(bbox, zs.max_bbox_deg(tol))
    with COMPUTE:
        return _overlays(st, plan, plan_id, bbox, box, kind, tol)


def _overlays(st, plan, plan_id, bbox, box, kind, tol) -> dict:
    ov, pend = st.od.overlays(plan_id, box)
    extra = {"build_id": st.od.build_id, "pending_sheets": _dedupe(pend)}
    empty = {
        "type": "FeatureCollection",
        "plan": zs.plan_ref(plan),
        "kind": kind,
        "simplify_m": tol,
        **extra,
        "features": [],
    }
    if ov is None or not len(ov):
        return empty
    _ = ov.sindex
    layers = PlanLayers(plan_id, None, ov, {}, simplify_levels(ov, "class_norm"))
    fc = zs.overlays_in_bbox(layers, plan, bbox, kind, tol)
    return {**fc, **extra, "features": fc["features"]}


@router.get("/zones/at")
async def get_zones_at(
    dist: str = Query(...),
    taluk: str = Query(...),
    hobli: str = Query(...),
    vlg: str = Query(...),
    survey: str = Query(...),
    road_width_m: float | None = Query(None, gt=0, le=120),
    authorization: str | None = Header(default=None),
) -> dict:
    _require_flag()
    fc = await zs.fetch_parcel(dist, taluk, hobli, vlg, survey, authorization)
    q = {"dist": dist, "taluk": taluk, "hobli": hobli, "vlg": vlg, "survey": survey}
    # merging sheets is CPU work: off the event loop, so other requests are not held up
    return await run_in_threadpool(_zones_at, fc, q, road_width_m, authorization)


def _zones_at(
    fc: dict,
    q: dict,
    road_width_m: float | None = None,
    authorization: str | None = None,
) -> dict:
    st = get_store()
    if st.od is not None:
        window = zs.parcel_geometry(fc)[0].buffer(WINDOW_M).envelope
        for plan_id in sorted(st.od.indexed_plans()):
            if _plan_enabled(plan_id):
                st.od.premerge(plan_id, window)  # worker merge, outside COMPUTE
    with COMPUTE, memtrace(f"/zones/at {q}"):
        res = _zones_at_locked(fc, q)
    # 1.20: plan roads (outside COMPUTE: the road estimate reads the village's parcels)
    res["abutting_roads"] = None
    if "feature.planning.roads" in _flags() and st.roads is not None:
        parcel = zs.parcel_geometry(fc)[0]
        res["abutting_roads"] = st.roads.abutting(
            parcel, q, road_width_m, authorization
        )
    return res


def _zones_at_locked(fc: dict, q: dict) -> dict:
    st = get_store()
    parcel, props = zs.parcel_geometry(fc)
    window = parcel.buffer(WINDOW_M).envelope
    zones, traces, skipped, used, pending = [], [], [], [], []
    plan_ids = sorted(st.od.indexed_plans()) if st.od is not None else []
    for plan_id in plan_ids:
        if not _plan_enabled(plan_id):
            skipped.append(plan_id)
            continue
        with memtrace(f"layers_for {plan_id}"):
            layers, pend = st.layers_for(plan_id, window)
        if layers is None:
            pending.extend(
                pend
            )  # a plan's hits are withheld until all its sheets here are ready
            continue
        used.append(layers)
        if layers.zones is not None and len(layers.zones):
            with memtrace(f"zone_hits {plan_id} n={len(layers.zones)}"):
                hits, trace = zs.zone_hits(layers, parcel)
            zones.extend(hits)
            traces.extend(trace)
    # 1.18: the village table's view next to the parcel's geometry answer
    from app.routers.registry import disagreement, village_summary

    vs = village_summary((q["dist"], q["taluk"], q["hobli"], q["vlg"]))
    share: dict[str, float] = {}
    for z in zones:
        a = (st.plans.get(z["plan_id"]) or {}).get("authority")
        if a:
            share[a] = share.get(a, 0.0) + z["overlap_pct"]
    here = sorted(share, key=lambda a: -share[a])
    statuses = {z["status"] for z in zones}
    note = None
    if pending:
        note = NOTE_PENDING
    elif not zones:
        note = "No zone from an enabled plan touches this parcel"
    elif statuses <= {"draft"}:
        note = "Only draft plans cover this parcel; shown for context only"
    return {
        "parcel": {
            **q,
            "village_name": props.get("village_name"),
            "area_sqm": round(parcel.area, 1),
        },
        "zones": zones,
        "trace_hits": traces,
        "plans_skipped": skipped,
        "overlays_nearby": zs.overlays_nearby(used, parcel),
        "note": note,
        "build_id": st.od.build_id if st.od is not None else None,
        "pending_sheets": _dedupe(pending),
        "village_summary": vs,
        "disagreement_note": disagreement(vs, here, "parcel") if here else None,
    }
