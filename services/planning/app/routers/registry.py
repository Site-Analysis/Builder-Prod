# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Plan registry, source register and authority lookup."""

from __future__ import annotations

import json
import os
import time

import numpy as np
import shapely
from fastapi import APIRouter, HTTPException, Query, Response
from pyproj import Transformer

from app.services.ondemand import COMPUTE
from app.services.store import CRS_METRIC, CRS_WGS84, get_store

_TO_METRIC = Transformer.from_crs(CRS_WGS84, CRS_METRIC, always_xy=True)
_TO_WGS = Transformer.from_crs(CRS_METRIC, CRS_WGS84, always_xy=True)

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
    od = get_store().od
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
                "extent": od.plan_extent(p["plan_id"]) if od is not None else None,
                "sheets": od.sheet_states(p["plan_id"]) if od is not None else [],
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
    "authority_no_master_plan",
    "no_master_plan_found",
]
NOTE_DISAGREE = "Most of village {v} is in {a}; this {what} is in {b}."
# notes on a plan's "Not coloured" rows that mean: this part of the LPA has no zone sheet
NO_SHEET_NOTES = (
    "LPA area on no published zone sheet",
    "LPA area on no detail sheet or hobli map",
)


AUTHORITY_WAIT_S = float(os.getenv("PLANNING_AUTHORITY_WAIT_S", "180"))


def _loaded(plan_id: str) -> bool:
    od = get_store().od
    return od is not None and plan_id in od.indexed_plans()


def _wait(fn, what: str):
    """Call fn until it returns something other than None (a needed sheet or outline is
    loading). /authority has no pending field, so a point query waits (open-decisions #42)."""
    t0 = time.time()
    while True:
        v = fn()
        if v is not None:
            return v
        if time.time() - t0 > AUTHORITY_WAIT_S:
            raise HTTPException(
                status_code=503,
                detail=f"{what} still loading; try again shortly",
                headers={"Retry-After": "10"},
            )
        time.sleep(1.0)


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


def no_master_plan(plan: dict) -> bool:
    """A register row that records an authority without a master plan (STRR, DPA)."""
    return "no master plan" in (plan.get("name") or "").lower()


def _plan_coverage(authority: str | None, plan_ids: list[str]) -> str:
    if authority is None:
        return "no_master_plan_found"
    if any(_loaded(p) for p in plan_ids):
        return "plan_loaded"
    plans = get_store().plans
    if any(p in plans and not no_master_plan(plans[p]) for p in plan_ids):
        return "plan_registered_not_loaded"
    own = [r for r in plans.values() if r.get("authority") == authority]
    # the authority's plan exists but has no zone map here (Madhure; a loaded plan leaving
    # the place uncoloured); an authority registered as having no master plan (STRR, DPA):
    # authority_no_master_plan (1.18); no register row at all: no plan was found
    if any(not no_master_plan(r) for r in own):
        return "lpa_no_zone_map"
    if own:
        return "authority_no_master_plan"
    return "no_master_plan_found"


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
    top_coverage: str | None = None,
) -> dict:
    entries = [e for e in entries if e["authority"] is not None]
    entries.sort(
        key=lambda e: -(e["share_pct"] if e["share_pct"] is not None else 100.0)
    )
    if entries:
        top = entries[0]
        pc = top_coverage or min(
            (e["plan_coverage"] for e in entries), key=PLAN_COVERAGE.index
        )
    else:
        top = _entry(None, "none", [], "no_master_plan_found", NOTE_OUTSIDE)
        top["source"] = "point" if point else None
        pc = "no_master_plan_found"
    if pc in ("no_master_plan_found", "authority_no_master_plan") and not sources:
        sources = [
            {k: _none(v) for k, v in r.items()} for r in get_store().sources_checked
        ]
    out = {"location": location}
    out.update({k: top[k] for k in TOP_KEYS})
    out["plan_coverage"] = pc
    out["authorities"] = entries
    out["sources_checked"] = sources or []
    od = get_store().od
    out["build_id"] = od.build_id if od is not None else None
    out["village_summary"] = None
    out["disagreement_note"] = None
    return out


def village_summary(key: tuple[str, str, str, str]) -> dict | None:
    """The village-table view of one village (1.18 VillageSummary), or None if unlisted."""
    row = get_store().authority.get(key)
    if row is None:
        return None
    entries, _s = _row_entries(row)
    pc = (row.get("plan_coverage") or "").strip() or (
        min((e["plan_coverage"] for e in entries), key=PLAN_COVERAGE.index)
        if entries
        else "no_master_plan_found"
    )
    authority = _none(row.get("authority")) if row.get("coverage") != "none" else None
    return {
        "dist": key[0],
        "taluk": key[1],
        "hobli": key[2],
        "vlg": key[3],
        "village_name": _none(row.get("village_name")),
        "authority": authority,
        "share_pct": float(row["share_pct"]) if row.get("share_pct") else None,
        "plan_coverage": pc,
    }


def disagreement(summary: dict | None, here: list[str], what: str) -> str | None:
    """Note when the village table's main authority is not where the point / parcel is."""
    if summary is None:
        return None
    va = summary.get("authority")
    if va is None and not here:
        return None
    if va in here:
        return None
    name = (summary.get("village_name") or summary["vlg"]).title()
    return NOTE_DISAGREE.format(
        v=name,
        a=va or "no LPA",
        b=", ".join(here) if here else "no LPA",
        what=what,
    )


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
        if not _loaded(p):
            continue

        def layers(p=p):
            lay, _pend = st.layers_for(p, pt.buffer(1.0).envelope)
            return lay

        z = _wait(layers, f"{p} zone sheets").zones
        if z is None or not len(z):
            continue
        with COMPUTE:
            hit = z.iloc[z.sindex.query(pt, predicate="intersects")]
        notes = hit["note"].fillna("").tolist() if len(hit) else []
        if notes and all(n in NO_SHEET_NOTES for n in notes):
            no_sheet = True
            continue
        return "plan_loaded"
    pc = _plan_coverage(authority, [p for p in plan_ids if not _loaded(p)])
    return "lpa_no_zone_map" if no_sheet and pc == "no_master_plan_found" else pc


def _zone_authorities(pt, seen: set) -> list[tuple[str, list[str]]]:
    """Authorities (not in `seen`) of loaded plans that have a zone sheet at the point."""
    st = get_store()
    od = st.od
    out = []
    if od is None:
        return out
    for plan_id in sorted(od.indexed_plans()):
        plan = st.plans.get(plan_id) or {}
        authority = plan.get("authority")
        if not authority or authority in seen:
            continue
        ext = od.plan_extent(plan_id)  # WGS84 bbox
        if ext is None:
            continue
        lng, lat = _TO_WGS.transform(pt.x, pt.y)
        if not (ext[0] <= lng <= ext[2] and ext[1] <= lat <= ext[3]):
            continue
        if _point_plan_coverage(authority, [plan_id], pt) == "plan_loaded":
            out.append((authority, _auth_plans(authority)))
    return out


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
        st_lpa, st_lpa_map = _wait(st.lpas, "LPA outlines")
        both = list(st_lpa.items()) + list(st_lpa_map.items())
        # geometry tests under the compute lock; zone lookups below take it themselves
        with COMPUTE:
            inside = [a for a, lpa in both if lpa.contains(pt)]
            near = {a: lpa.distance(pt) for a, lpa in both}
        # plan LPAs first (a loaded plan's own extent, e.g. Anekal before STRR), then
        # BMRDA's LPA map for authorities without a loaded plan
        for authority in inside:
            if authority in seen:
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
        # F1 (1.18): answers come from geometry. A loaded plan with a zone at the point
        # counts even where its LPA outline (another source) leaves the point just outside
        for authority, pids in _zone_authorities(pt, seen):
            seen.add(authority)
            entries.append(
                _entry(authority, "full", pids, "plan_loaded", source="point")
            )
        top = None
        if not entries:
            # a point between two LPAs whose boundaries come from different sources (a plan's
            # own LPA vs BMRDA's LPA map, ~15 m apart at the median): the LPAs within
            # EDGE_TOLERANCE_M, partial, with a note (step E: no gap along shared edges).
            # F9 (1.18): plan_loaded only where a loaded zone covers the point
            for authority, _lpa in both:
                if authority in seen or near[authority] > EDGE_TOLERANCE_M:
                    continue
                seen.add(authority)
                pids = _auth_plans(authority)
                entries.append(
                    _entry(
                        authority,
                        "partial",
                        pids,
                        _point_plan_coverage(authority, pids, pt),
                        NOTE_NEAR_EDGE.format(d=round(near[authority])),
                        source="point",
                    )
                )
            if entries:
                top = (
                    "plan_loaded"
                    if any(e["plan_coverage"] == "plan_loaded" for e in entries)
                    else "no_master_plan_found"
                )
        res = _result(loc, entries, point=True, top_coverage=top)
        key = st.villages.lookup(x, y) if st.villages is not None else None
        if key is not None:
            vs = village_summary(key)
            res["village_summary"] = vs
            here = [
                e["authority"]
                for e in res["authorities"]
                if e["coverage"] == "full" and e["authority"]
            ]
            res["disagreement_note"] = disagreement(vs, here, "location")
        return res
    raise HTTPException(
        status_code=400,
        detail="Pass all of dist, taluk, hobli, vlg, or both lat and lng",
    )


_COVERAGE_FLAG = "feature.planning.coverage-layer"
COVERAGE_MAX_DEG = 0.5


@router.get("/coverage")
def get_coverage(bbox: str = Query(...)) -> Response:
    """Villages coloured by plan_coverage (side panel "Coverage status" layer, 1.19)."""
    _require_flag()
    if _COVERAGE_FLAG not in _flags():
        raise HTTPException(
            status_code=403, detail=f"Feature flag disabled: {_COVERAGE_FLAG}"
        )
    try:
        x0, y0, x1, y1 = (float(v) for v in bbox.split(","))
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="bbox must be minLng,minLat,maxLng,maxLat"
        ) from exc
    if x1 <= x0 or y1 <= y0:
        raise HTTPException(status_code=400, detail="bbox min must be below max")
    if x1 - x0 > COVERAGE_MAX_DEG + 1e-9 or y1 - y0 > COVERAGE_MAX_DEG + 1e-9:
        raise HTTPException(
            status_code=400,
            detail=f"bbox larger than {COVERAGE_MAX_DEG} degrees per side",
        )
    st = get_store()
    box = shapely.transform(
        shapely.box(x0, y0, x1, y1),
        lambda xy: np.column_stack(_TO_METRIC.transform(xy[:, 0], xy[:, 1])),
    )
    vi = st.villages
    feats = []
    for key, gj in vi.geojson_in(box) if vi is not None else []:
        vs = village_summary(key)
        if vs is None:
            continue
        props = {
            k: vs[k]
            for k in (
                "dist",
                "taluk",
                "hobli",
                "vlg",
                "village_name",
                "authority",
                "plan_coverage",
            )
        }
        feats.append(
            '{"type":"Feature","geometry":'
            + gj
            + ',"properties":'
            + json.dumps(props)
            + "}"
        )
    state = vi.state if vi is not None else "unavailable"
    state = {"not_started": "loading", "off": "unavailable"}.get(state, state)
    od = st.od
    head = {
        "type": "FeatureCollection",
        "state": state,
        "build_id": od.build_id if od is not None else None,
    }
    body = json.dumps(head)[:-1] + ',"features":[' + ",".join(feats) + "]}"
    return Response(content=body, media_type="application/json")
