# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

from __future__ import annotations

import json
import os

# numpy's OpenBLAS reserves a buffer per thread (~800 MB committed on 32 CPUs); nothing here
# needs threaded BLAS, so one thread keeps the 2 GB worker / 1 GB service caps honest
for _v in (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ.setdefault(_v, "1")
# Arrow's default pool (mimalloc) keeps freed pages: decoding cached chunks then grows the
# service's memory without bound; the system allocator returns them
os.environ.setdefault("ARROW_DEFAULT_MEMORY_POOL", "system")

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.auth import verify_token
from app.routers.registry import router as registry_router
from app.routers.zones import router as zones_router
from app.services.ondemand import wipe_temp
from app.services.store import get_store


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # the temp folder holds only in-flight downloads; wipe it at start and stop (folders of
    # a running index build or detached job are locked and kept)
    wipe_temp()
    get_store()  # register + layer index; LPA outlines start loading in the background
    yield
    st = get_store()
    if st.od is not None:
        st.od.stop()
    wipe_temp()


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
