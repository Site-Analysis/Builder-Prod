# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Land records endpoints: survey search and hierarchy listing
(districts / taluks / hoblis / villages) for the dropdown cascade.
"""

from __future__ import annotations

import os
from typing import Any

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request

from app.services import cadastral_service as cs

_LAND_FLAG = "feature.cadastral.land-records"

router = APIRouter(tags=["land-records"])


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


@router.get("/search")
async def search_survey(
    request: Request, q: str = Query(..., min_length=2)
) -> list[dict[str, Any]]:
    """Survey number prefix search across all indexed Karnataka parcels (max 25 results)."""
    _require_flag()
    return await cs.search_survey(_pool(request), q.strip())


@router.get("/districts")
async def list_districts(request: Request) -> list[dict[str, str]]:
    """Districts with human-readable names."""
    _require_flag()
    return await cs.list_districts(_pool(request))


@router.get("/taluks")
async def list_taluks(
    request: Request,
    dist: str = Query(..., description="District code"),
) -> list[dict[str, str]]:
    """Taluks with names for a given district code."""
    _require_flag()
    return await cs.list_taluks(_pool(request), dist)


@router.get("/hoblis")
async def list_hoblis(
    request: Request,
    dist: str = Query(...),
    taluk: str = Query(...),
) -> list[dict[str, str]]:
    """Hoblis with names for a given district + taluk."""
    _require_flag()
    return await cs.list_hoblis(_pool(request), dist, taluk)


@router.get("/villages")
async def list_villages(
    request: Request,
    dist: str = Query(...),
    taluk: str = Query(...),
    hobli: str = Query(...),
) -> list[dict[str, str]]:
    """Villages with names for a given district + taluk + hobli."""
    _require_flag()
    return await cs.list_villages(_pool(request), dist, taluk, hobli)
