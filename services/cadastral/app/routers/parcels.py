# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Parcel geometry endpoint: e-Chawadi Bhoomi parcel polygons in WGS84 GeoJSON."""

from __future__ import annotations

import os

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from app.services import cadastral_service as cs

_LAND_FLAG = "feature.cadastral.land-records"

router = APIRouter(tags=["parcels"])


def _require_flag() -> None:
    enabled = {f.strip() for f in os.getenv("FLAGS", "").split(",") if f.strip()}
    if _LAND_FLAG not in enabled:
        raise HTTPException(
            status_code=403, detail=f"Feature flag disabled: {_LAND_FLAG}"
        )


def _pool(request: Request) -> asyncpg.Pool:
    pool = request.app.state.pool
    if pool is None:
        raise HTTPException(status_code=503, detail="Database not configured")
    return pool


@router.get("/data")
async def get_parcel_data(
    request: Request,
    dist: str = Query(...),
    taluk: str = Query(...),
    hobli: str = Query(...),
    vlg: str = Query(...),
    survey: str | None = Query(None, description="Filter to exact survey_no"),
) -> Response:
    """Parcel polygon GeoJSON for a village."""
    _require_flag()
    geojson = await cs.build_geojson(_pool(request), dist, taluk, hobli, vlg, survey)
    return Response(content=geojson, media_type="application/json")
