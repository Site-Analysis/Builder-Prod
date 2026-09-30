# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.auth import verify_token
from app.routers.registry import router as registry_router
from app.routers.zones import router as zones_router
from app.services.store import get_store


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_store()  # load register + GeoParquet layers and build STRtrees at startup
    yield


app = FastAPI(
    title="Planning Service",
    version="1.11.0",
    description=(
        "Bengaluru statutory planning layers (2031 plans). Every record carries the "
        "status of the document it came from; BDA RMP 2031 is a draft, never approved. "
        "Gated by feature.planning.layers and feature.planning.plan.<plan_id>."
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

# GeoJSON layers compress ~5x; clients send Accept-Encoding: gzip
app.add_middleware(GZipMiddleware, minimum_size=1024)

app.include_router(registry_router, dependencies=[Depends(verify_token)])
app.include_router(zones_router, dependencies=[Depends(verify_token)])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "planning"}
