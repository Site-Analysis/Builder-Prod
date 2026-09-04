# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager

import asyncpg
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import verify_token
from app.routers.land_records import router as land_router
from app.routers.parcels import router as parcel_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        app.state.pool = await asyncpg.create_pool(
            db_url,
            min_size=2,
            max_size=20,
            command_timeout=15,
        )
    else:
        app.state.pool = None
    yield
    if app.state.pool:
        await app.state.pool.close()


app = FastAPI(
    title="Cadastral Service",
    version="1.1.0",
    description=(
        "Karnataka e-Chawadi (Bhoomi) cadastral data: parcel geometries and "
        "administrative hierarchy (district / taluk / hobli / village). "
        "Gated by feature.cadastral.land-records."
    ),
    lifespan=lifespan,
)

_raw = os.getenv("CORS_ORIGINS", '["http://localhost:3000"]')
try:
    _origins = json.loads(_raw)
except (json.JSONDecodeError, ValueError):
    _origins = [o.strip() for o in _raw.split(",") if o.strip()] or ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials="*" not in _origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(land_router, dependencies=[Depends(verify_token)])
app.include_router(parcel_router, dependencies=[Depends(verify_token)])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "cadastral"}
