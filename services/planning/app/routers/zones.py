# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Zone and overlay layers, and zones touching a parcel."""

from __future__ import annotations

import os

from fastapi import APIRouter, Header, HTTPException, Query

from app.services import zones_service as zs
from app.services.store import get_store

_LAYERS_FLAG = "feature.planning.layers"

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


def _plan_and_layers(plan_id: str):
    st = get_store()
    plan = st.plans.get(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"Unknown plan_id: {plan_id}")
    if not _plan_enabled(plan_id):
        raise HTTPException(
            status_code=403,
            detail=f"Feature flag disabled: feature.planning.plan.{plan_id}",
        )
    return plan, st.layers.get(plan_id)


@router.get("/zones")
def get_zones(plan_id: str = Query(...), bbox: str = Query(...)) -> dict:
    _require_flag()
    plan, layers = _plan_and_layers(plan_id)
    if layers is None or layers.zones is None:
        raise HTTPException(
            status_code=404, detail=f"No zone layer loaded for {plan_id}"
        )
    return zs.zones_in_bbox(layers, plan, bbox)


@router.get("/overlays")
def get_overlays(
    plan_id: str = Query(...), bbox: str = Query(...), kind: str = Query(...)
) -> dict:
    _require_flag()
    plan, layers = _plan_and_layers(plan_id)
    if layers is None or layers.overlays is None:
        raise HTTPException(
            status_code=404, detail=f"No overlay layer loaded for {plan_id}"
        )
    return zs.overlays_in_bbox(layers, plan, bbox, kind)


@router.get("/zones/at")
async def get_zones_at(
    dist: str = Query(...),
    taluk: str = Query(...),
    hobli: str = Query(...),
    vlg: str = Query(...),
    survey: str = Query(...),
    authorization: str | None = Header(default=None),
) -> dict:
    _require_flag()
    st = get_store()
    fc = await zs.fetch_parcel(dist, taluk, hobli, vlg, survey, authorization)
    parcel, props = zs.parcel_geometry(fc)
    zones, skipped, used = [], [], []
    for plan_id, layers in st.layers.items():
        if not _plan_enabled(plan_id):
            skipped.append(plan_id)
            continue
        used.append(layers)
        if layers.zones is not None:
            zones.extend(zs.zone_hits(layers, parcel))
    statuses = {z["status"] for z in zones}
    note = None
    if not zones:
        note = "No zone from an enabled plan touches this parcel"
    elif statuses <= {"draft"}:
        note = "Only draft plans cover this parcel; shown for context only"
    return {
        "parcel": {
            "dist": dist,
            "taluk": taluk,
            "hobli": hobli,
            "vlg": vlg,
            "survey": survey,
            "village_name": props.get("village_name"),
            "area_sqm": round(parcel.area, 1),
        },
        "zones": zones,
        "plans_skipped": skipped,
        "overlays_nearby": zs.overlays_nearby(used, parcel),
        "note": note,
    }
