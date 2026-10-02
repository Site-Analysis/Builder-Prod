# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Plan registry, source register and authority lookup."""

from __future__ import annotations

import json
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
                "loaded": _loaded(p["plan_id"]),
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


NOTE_OUTSIDE = "Outside BDA; this area's plan isn't loaded yet"
NOTE_NO_PLAN = "No planning authority or master plan found for this location"
EDGE_TOLERANCE_M = 100.0
NOTE_NEAR_EDGE = "Outside the LPA by {d} m; boundary sources differ by about 15 m here."
# plan_coverage order: the first that applies is the location's value
PLAN_COVERAGE = [
    "plan_loaded",
    "plan_registered_not_loaded",
    "lpa_no_zone_map",
    "no_master_plan_found",
]
# notes on a plan's "Not coloured" rows that mean: this part of the LPA has no zone sheet
NO_SHEET_NOTES = (
    "LPA area on no published zone sheet",
    "LPA area on no detail sheet or hobli map",
)


def _loaded(plan_id: str) -> bool:
    lay = get_store().layers.get(plan_id)
    return lay is not None and lay.zones is not None


def _doc_ids(plan_id: str) -> list[str]:
    return sorted(
        d["doc_id"]
        for d in get_store().docs.values()
        if d["plan_id"] == plan_id and d["status"] != "superseded"
    )


def _plan_ref(plan: dict, coverage: str) -> dict:
    return {
        "plan_id": plan["plan_id"],
        "status": plan["status"],
        "status_label": plan["status_label"],
        "status_condition": _none(plan.get("status_condition")),
        "go_ref": _none(plan.get("go_ref")),
        "coverage": coverage,
        "loaded": _loaded(plan["plan_id"]),
        "doc_ids": _doc_ids(plan["plan_id"]),
    }


def _auth_plans(authority: str | None) -> list[str]:
    a = get_store().authorities.get(authority or "") or {}
    return [p for p in (a.get("plan_ids") or "").split(";") if p]


def _plan_coverage(authority: str | None, plan_ids: list[str]) -> str:
    if authority is None:
        return "no_master_plan_found"
    if any(_loaded(p) for p in plan_ids):
        return "plan_loaded"
    if any(p in get_store().plans for p in plan_ids):
        return "plan_registered_not_loaded"
    return "lpa_no_zone_map"


def _entry(
    authority: str | None,
    coverage: str,
    plan_ids: list[str] | None = None,
    plan_coverage: str | None = None,
    note: str | None = None,
    **extra,
) -> dict:
    st = get_store()
    a = st.authorities.get(authority or "")
    if plan_ids is None:
        plan_ids = _auth_plans(authority)
    plans = [st.plans[p] for p in plan_ids if p in st.plans]
    pcov = "full" if coverage == "full" else "partial"
    operative = [p for p in plans if p["status"] == "final"]
    return {
        "authority": authority if a else None,
        "lpa": a["lpa_label"] if a else None,
        "coverage": coverage if a else "none",
        "share_pct": extra.get("share_pct"),
        "pd": extra.get("pd"),
        "source": extra.get("source"),
        "mismatch_note": extra.get("mismatch_note"),
        "plan_coverage": plan_coverage
        or _plan_coverage(authority if a else None, plan_ids),
        # BDA: RMP 2015 (operative) is not loaded; only the draft RMP 2031 is
        "operative_plan": _plan_ref(operative[0], pcov) if operative else None,
        "draft_plans": [_plan_ref(p, pcov) for p in plans if p["status"] == "draft"],
        "note": note if note is not None else (_none(a["note"]) if a else None),
        "sources_checked": extra.get("sources_checked") or [],
    }


TOP_KEYS = (
    "authority",
    "lpa",
    "coverage",
    "share_pct",
    "pd",
    "source",
    "mismatch_note",
    "operative_plan",
    "draft_plans",
    "note",
)


def _result(
    location: dict,
    entries: list[dict],
    sources: list[dict] | None = None,
    point: bool = False,
) -> dict:
    entries = [e for e in entries if e["authority"] is not None]
    entries.sort(
        key=lambda e: -(e["share_pct"] if e["share_pct"] is not None else 100.0)
    )
    if entries:
        top = entries[0]
        pc = min((e["plan_coverage"] for e in entries), key=PLAN_COVERAGE.index)
    else:
        top = _entry(None, "none", [], "no_master_plan_found", NOTE_OUTSIDE)
        top["source"] = "point" if point else None
        pc = "no_master_plan_found"
    if pc == "no_master_plan_found" and not sources:
        sources = [
            {k: _none(v) for k, v in r.items()} for r in get_store().sources_checked
        ]
    out = {"location": location}
    out.update({k: top[k] for k in TOP_KEYS})
    out["plan_coverage"] = pc
    out["authorities"] = entries
    out["sources_checked"] = sources or []
    return out


def _row_entries(row: dict) -> tuple[list[dict], list[dict]]:
    """Village row -> entries. 1.16 rows carry authorities_json / sources_checked_json;
    older rows have one authority in the flat columns."""
    raw = (row.get("authorities_json") or "").strip()
    srcs_raw = (row.get("sources_checked_json") or "").strip()
    srcs = json.loads(srcs_raw) if srcs_raw else []
    if raw:
        return [
            _entry(
                e.get("authority"),
                e.get("coverage", "full"),
                e.get("plan_ids"),
                e.get("plan_coverage"),
                e.get("note"),
                share_pct=e.get("share_pct"),
                pd=e.get("pd"),
                source=e.get("source"),
                mismatch_note=e.get("mismatch_note"),
                sources_checked=e.get("sources_checked"),
            )
            for e in json.loads(raw)
        ], srcs
    authority = _none(row["authority"]) if row["coverage"] != "none" else None
    if authority is None:
        return [], srcs
    return [
        _entry(
            authority,
            row["coverage"],
            [p for p in (row.get("plan_ids") or "").split(";") if p],
            share_pct=float(row["share_pct"]) if row["share_pct"] else None,
            pd=int(row["pd"]) if row["pd"] else None,
            source=_none(row["source"]),
            mismatch_note=_none(row["mismatch_note"]),
        )
    ], srcs


def _point_plan_coverage(authority: str, plan_ids: list[str], pt) -> str:
    """plan_loaded where a loaded plan has a zone sheet at the point; a loaded plan whose
    zone there is its 'no sheet' area counts as lpa_no_zone_map."""
    st = get_store()
    no_sheet = False
    for p in plan_ids:
        lay = st.layers.get(p)
        if lay is None or lay.zones is None:
            continue
        z = lay.zones
        hit = z.iloc[z.sindex.query(pt, predicate="intersects")]
        notes = hit["note"].fillna("").tolist() if len(hit) else []
        if notes and all(n in NO_SHEET_NOTES for n in notes):
            no_sheet = True
            continue
        return "plan_loaded"
    pc = _plan_coverage(authority, [p for p in plan_ids if not _loaded(p)])
    return "lpa_no_zone_map" if no_sheet and pc == "no_master_plan_found" else pc


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
            return _result(loc, [])
        entries, srcs = _row_entries(row)
        res = _result(loc, entries, srcs)
        if (row.get("plan_coverage") or "").strip():
            res["plan_coverage"] = row["plan_coverage"].strip()
        return res
    if lat is not None and lng is not None:
        loc = {"lat": lat, "lng": lng}
        x, y = _TO_METRIC.transform(lng, lat)
        pt = shapely.Point(x, y)
        entries, seen = [], set()
        # plan LPAs first (a loaded plan's own extent, e.g. Anekal before STRR), then
        # BMRDA's LPA map for authorities without a loaded plan
        for authority, lpa in list(st.lpa.items()) + list(st.lpa_map.items()):
            if authority in seen or not lpa.contains(pt):
                continue
            seen.add(authority)
            pids = _auth_plans(authority)
            entries.append(
                _entry(
                    authority,
                    "full",
                    pids,
                    _point_plan_coverage(authority, pids, pt),
                    source="point",
                )
            )
        if not entries:
            # a point between two LPAs whose boundaries come from different sources (a plan's
            # own LPA vs BMRDA's LPA map, ~15 m apart at the median): the LPAs within
            # EDGE_TOLERANCE_M, partial, with a note (step E: no gap along shared edges)
            for authority, lpa in list(st.lpa.items()) + list(st.lpa_map.items()):
                if authority in seen or lpa.distance(pt) > EDGE_TOLERANCE_M:
                    continue
                seen.add(authority)
                pids = _auth_plans(authority)
                entries.append(
                    _entry(
                        authority,
                        "partial",
                        pids,
                        _point_plan_coverage(authority, pids, pt),
                        NOTE_NEAR_EDGE.format(d=round(lpa.distance(pt))),
                        source="point",
                    )
                )
        return _result(loc, entries, point=True)
    raise HTTPException(
        status_code=400,
        detail="Pass all of dist, taluk, hobli, vlg, or both lat and lng",
    )
