# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Core cadastral data access — CockroachDB via asyncpg pool.

Pool is created in app lifespan (main.py) and injected via request.app.state.pool.
All public functions are async and accept a pool argument.

Hierarchy (districts/taluks/hoblis/villages) served from DB tables.
Parcels served from DB via ST_AsGeoJSON — no filesystem reads at runtime.

Filesystem fallback (load_village, find_paths, build_geojson) retained for
local dev without DB; disabled when DATABASE_URL is set.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# Kept for local-dev filesystem fallback only (no DATABASE_URL).
# X/Y swap: compensates for scraper bug — Polygon(Northing, Easting) stored instead of
# Polygon(Easting, Northing) in EPSG:32643. Applied during migration; not at query time.
_SWAP_XY = [0, 1, 1, 0, 0, 0]


# ── CockroachDB path ──────────────────────────────────────────────────────────


async def list_districts(pool: asyncpg.Pool) -> list[dict[str, str]]:
    rows = await pool.fetch("SELECT dist_code, name FROM districts ORDER BY name")
    return [{"code": str(r["dist_code"]), "name": r["name"]} for r in rows]


async def list_taluks(pool: asyncpg.Pool, dist: str) -> list[dict[str, str]]:
    rows = await pool.fetch(
        "SELECT taluk_code, name FROM taluks WHERE dist_code = $1 ORDER BY name",
        int(dist),
    )
    return [{"code": str(r["taluk_code"]), "name": r["name"]} for r in rows]


async def list_hoblis(
    pool: asyncpg.Pool, dist: str, taluk: str
) -> list[dict[str, str]]:
    rows = await pool.fetch(
        "SELECT hobli_code, name FROM hoblis WHERE dist_code = $1 AND taluk_code = $2 ORDER BY name",
        int(dist),
        int(taluk),
    )
    return [{"code": str(r["hobli_code"]), "name": r["name"]} for r in rows]


async def list_villages(
    pool: asyncpg.Pool, dist: str, taluk: str, hobli: str
) -> list[dict[str, str]]:
    rows = await pool.fetch(
        "SELECT vlg_code, name FROM villages "
        "WHERE dist_code = $1 AND taluk_code = $2 AND hobli_code = $3 ORDER BY name",
        int(dist),
        int(taluk),
        int(hobli),
    )
    return [
        {"code": str(r["vlg_code"]), "name": r["name"] or str(r["vlg_code"])}
        for r in rows
    ]


async def build_geojson(
    pool: asyncpg.Pool,
    dist: str,
    taluk: str,
    hobli: str,
    vlg: str,
    survey: str | None = None,
) -> str:
    village_code = f"{dist}_{taluk}_{hobli}_{vlg}"
    if survey:
        rows = await pool.fetch(
            "SELECT ST_AsGeoJSON(geom) AS geom, survey_no "
            "FROM parcels WHERE village_code = $1 AND survey_no = $2",
            village_code,
            survey,
        )
    else:
        rows = await pool.fetch(
            "SELECT ST_AsGeoJSON(geom) AS geom, survey_no "
            "FROM parcels WHERE village_code = $1",
            village_code,
        )
    features = [
        {
            "type": "Feature",
            "geometry": json.loads(r["geom"]),
            "properties": {"survey_no": r["survey_no"]},
        }
        for r in rows
        if r["geom"]
    ]
    return json.dumps({"type": "FeatureCollection", "features": features})


async def search_survey(
    pool: asyncpg.Pool, q: str, limit: int = 25
) -> list[dict[str, Any]]:
    q_norm = q.split("/")[0].strip()
    if len(q_norm) < 2:
        return []
    rows = await pool.fetch(
        "SELECT DISTINCT survey_no, survey_no_norm, village_code "
        "FROM parcels WHERE survey_no_norm LIKE $1 "
        "ORDER BY survey_no_norm LIMIT $2",
        q_norm + "%",
        limit,
    )
    results = []
    for r in rows:
        parts = r["village_code"].split("_")
        results.append(
            {
                "survey_no": r["survey_no"],
                "village_name": "",
                "dist": parts[0] if len(parts) > 0 else "",
                "taluk": parts[1] if len(parts) > 1 else "",
                "hobli": parts[2] if len(parts) > 2 else "",
                "vlg": parts[3] if len(parts) > 3 else "",
            }
        )
    return results
