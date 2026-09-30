# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Plan registry, source register and authority lookup."""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Query

from app.services.store import get_store

_LAYERS_FLAG = "feature.planning.layers"

router = APIRouter(tags=["registry"])


def _flags() -> set[str]:
    return set(os.getenv("FLAGS", "").split())


def _require_flag() -> None:
    if _LAYERS_FLAG not in _flags():
        raise HTTPException(
            status_code=403, detail=f"Feature flag disabled: {_LAYERS_FLAG}"
        )


def _none(v: str | None) -> str | None:
    return v if v not in (None, "") else None


@router.get("/plans")
def list_plans() -> list[dict]:
    _require_flag()
    enabled = _flags()
    out = []
    for p in get_store().plans.values():
        out.append(
            {
                "plan_id": p["plan_id"],
                "authority": p["authority"],
                "name": p["name"],
                "horizon": int(p["horizon"]) if p.get("horizon") else None,
                "status": p["status"],
                "status_label": p["status_label"],
                "go_ref": _none(p.get("go_ref")),
                "go_date": _none(p.get("go_date")),
                "operative_for": _none(p.get("operative_for")),
                "checked": p["checked"],
                "notes": _none(p.get("notes")),
                "enabled": f"feature.planning.plan.{p['plan_id']}" in enabled,
            }
        )
    return out


@router.get("/docs/{doc_id}")
def get_doc(doc_id: str) -> dict:
    _require_flag()
    d = get_store().docs.get(doc_id)
    if d is None:
        raise HTTPException(status_code=404, detail=f"Unknown doc_id: {doc_id}")
    return {
        k: (
            _none(v)
            if k in ("go_ref", "go_date", "applies_to", "amends", "superseded_by")
            else v
        )
        for k, v in d.items()
    }


@router.get("/authority")
def get_authority(
    dist: str | None = Query(None),
    taluk: str | None = Query(None),
    hobli: str | None = Query(None),
    vlg: str | None = Query(None),
    lat: float | None = Query(None),
    lng: float | None = Query(None),
) -> dict:
    _require_flag()
    raise HTTPException(
        status_code=501,
        detail="Not implemented yet (authority table arrives in build step 1.7)",
    )
