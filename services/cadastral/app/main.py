# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

from __future__ import annotations

import json
import os

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import verify_token
from app.routers.land_records import router as land_router
from app.routers.parcels import router as parcel_router

app = FastAPI(
    title="Cadastral Service",
    version="1.0.0",
    description=(
        "Karnataka e-Chawadi (Bhoomi) cadastral data: parcel geometries and "
        "administrative hierarchy (district / taluk / hobli / village). "
        "Gated by feature.cadastral.land-records."
    ),
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
