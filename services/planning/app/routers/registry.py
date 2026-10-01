# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Plan registry, source register and authority lookup."""

from __future__ import annotations

import os

import shapely
from fastapi import APIRouter, HTTPException, Query
from pyproj import Transformer

from app.services.store import CRS_METRIC, CRS_WGS84, get_store

_TO_METRIC = Transformer.from_crs(CRS_WGS84, CRS_METRIC, always_xy=True)

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
                "status_condition": _none(p.get("status_condition")),
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
            if k
            in (
                "go_ref",
                "go_date",
                "applies_to",
                "amends",
                "superseded_by",
                "status_condition",
            )
            else v
        )
        for k, v in ({"status_condition": None} | d).items()
    }


BDA_PLAN = "BDA-RMP2031"
NOTE_BDA = "Only a draft plan is loaded for this area"
NOTE_OUTSIDE = "Outside BDA; this area's plan isn't loaded yet"


def _plan_ref(plan: dict, coverage: str) -> dict:
    return {
        "plan_id": plan["plan_id"],
        "status": plan["status"],
        "status_label": plan["status_label"],
        "status_condition": _none(plan.get("status_condition")),
        "go_ref": _none(plan.get("go_ref")),
        "coverage": coverage,
    }


def _result(location: dict, bda: bool, coverage: str, **extra) -> dict:
    st = get_store()
    plan = st.plans.get(BDA_PLAN)
    return {
        "location": location,
        "authority": "BDA" if bda else None,
        "lpa": "LPA of BDA" if bda else None,
        "coverage": coverage if bda else "none",
        "share_pct": extra.get("share_pct"),
        "pd": extra.get("pd"),
        "source": extra.get("source"),
        "mismatch_note": extra.get("mismatch_note"),
        "operative_plan": None,  # RMP 2015 (operative for BDA) is not loaded yet
        "draft_plans": [_plan_ref(plan, "full" if coverage == "full" else "partial")]
        if bda and plan
        else [],
        "note": NOTE_BDA if bda else NOTE_OUTSIDE,
    }


@router.get("/authority")
def get_authority(
    dist: str | None = Query(None),
    taluk: str | None = Query(None),
    hobli: str | None = Query(None),
    vlg: str | None = Query(None),
    lat: float | None = Query(None, ge=-90, le=90),
    lng: float | None = Query(None, ge=-180, le=180),
) -> dict:
    _require_flag()
    st = get_store()
    codes = (dist, taluk, hobli, vlg)
    if all(codes):
        loc = {"dist": dist, "taluk": taluk, "hobli": hobli, "vlg": vlg}
        row = st.authority.get(codes)
        if row is None:
            if dist in st.authority_dists:  # district fully listed: the code is unknown
                raise HTTPException(
                    status_code=404, detail="Village codes do not exist"
                )
            return _result(loc, False, "none")
        bda = row["authority"] == "BDA"
        return _result(
            loc,
            bda,
            row["coverage"],
            share_pct=float(row["share_pct"]) if row["share_pct"] else None,
            pd=int(row["pd"]) if row["pd"] else None,
            source=_none(row["source"]),
            mismatch_note=_none(row["mismatch_note"]),
        )
    if lat is not None and lng is not None:
        loc = {"lat": lat, "lng": lng}
        lpa = st.lpa.get("BDA")
        x, y = _TO_METRIC.transform(lng, lat)
        inside = bool(lpa is not None and lpa.contains(shapely.Point(x, y)))
        return _result(loc, inside, "full" if inside else "none", source="point")
    raise HTTPException(
        status_code=400,
        detail="Pass all of dist, taluk, hobli, vlg, or both lat and lng",
    )
