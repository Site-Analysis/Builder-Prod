# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Cadastral service smoke tests — Phase 1C (CockroachDB).

Covers:
  (a) /health → {status: ok, service: cadastral}
  (b) all land-record endpoints → 403 without flag
  (c) /districts → list[{code, name}]
  (d) /taluks?dist=1 → list shape
  (e) /hoblis?dist=1&taluk=9 → list shape
  (f) /villages?dist=1&taluk=9&hobli=3 → list shape
  (g) /search short query → 422
  (h) /data → GeoJSON FeatureCollection
  (i) /search → list (empty OK; shape checked if data present)

MockPool replaces asyncpg.Pool — no real DB needed in CI.

Run: pytest tests/cadastral_smoke.py
Requires: cd services/cadastral && pip install -r requirements.txt
"""

from __future__ import annotations

import sys
from pathlib import Path

_SVC = Path(__file__).resolve().parents[1] / "services" / "cadastral"
if str(_SVC) in sys.path:
    sys.path.remove(str(_SVC))
sys.path.insert(0, str(_SVC))
sys.modules.pop("app", None)

import pytest  # noqa: E402

try:
    import geopandas  # noqa: F401

    _HAS_GEOPANDAS = True
except ImportError:
    _HAS_GEOPANDAS = False

if not _HAS_GEOPANDAS:
    pytest.skip(
        "geopandas not installed. Run: cd services/cadastral && pip install -r requirements.txt",
        allow_module_level=True,
    )

from fastapi.testclient import TestClient  # noqa: E402

_LAND_FLAG = "feature.cadastral.land-records"
_DUMMY_PAYLOAD = {"sub": "test-user", "preferred_username": "smoke-test"}


class _MockPool:
    """Async mock of asyncpg.Pool — returns minimal valid rows per query."""

    async def fetch(self, query: str, *args: object) -> list[dict]:
        q = query.lower()
        if "from districts" in q:
            return [{"dist_code": 1, "name": "Test District"}]
        if "from taluks" in q:
            return [{"taluk_code": 9, "name": "Test Taluk"}]
        if "from hoblis" in q:
            return [{"hobli_code": 3, "name": "Test Hobli"}]
        if "from villages" in q:
            return [{"vlg_code": 46, "name": "Test Village"}]
        return []

    async def close(self) -> None:
        pass


def _make_client(monkeypatch, flags: str):
    monkeypatch.setenv("FLAGS", flags)
    sys.modules.pop("app", None)
    sys.modules.pop("app.main", None)
    sys.modules.pop("app.auth", None)
    from app.auth import verify_token
    from app.main import app

    app.dependency_overrides[verify_token] = lambda: _DUMMY_PAYLOAD
    # Use TestClient as context manager so lifespan runs, then inject mock pool.
    client = TestClient(app, raise_server_exceptions=True)
    client.__enter__()
    app.state.pool = _MockPool()
    return client, app


@pytest.fixture
def client(monkeypatch):
    c, app = _make_client(monkeypatch, _LAND_FLAG)
    yield c
    app.dependency_overrides.clear()
    c.__exit__(None, None, None)


@pytest.fixture
def client_no_flags(monkeypatch):
    c, app = _make_client(monkeypatch, "")
    yield c
    app.dependency_overrides.clear()
    c.__exit__(None, None, None)


def test_a_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "cadastral"


def test_b_flag_guard(client_no_flags):
    """All gated endpoints return 403 without flag."""
    for path in [
        "/districts",
        "/taluks?dist=1",
        "/hoblis?dist=1&taluk=9",
        "/villages?dist=1&taluk=9&hobli=3",
        "/data?dist=1&taluk=9&hobli=3&vlg=46",
        "/search?q=30",
    ]:
        r = client_no_flags.get(path)
        assert r.status_code == 403, f"Expected 403 for {path}, got {r.status_code}"


def test_c_districts_shape(client):
    r = client.get("/districts")
    assert r.status_code == 200
    results = r.json()
    assert isinstance(results, list)
    assert len(results) > 0
    assert {"code", "name"} <= set(results[0].keys())


def test_d_taluks_shape(client):
    r = client.get("/taluks?dist=1")
    assert r.status_code == 200
    results = r.json()
    assert isinstance(results, list)
    assert len(results) > 0
    assert {"code", "name"} <= set(results[0].keys())


def test_e_hoblis_shape(client):
    r = client.get("/hoblis?dist=1&taluk=9")
    assert r.status_code == 200
    results = r.json()
    assert isinstance(results, list)
    assert len(results) > 0
    assert {"code", "name"} <= set(results[0].keys())


def test_f_villages_shape(client):
    r = client.get("/villages?dist=1&taluk=9&hobli=3")
    assert r.status_code == 200
    results = r.json()
    assert isinstance(results, list)
    assert len(results) > 0
    assert {"code", "name"} <= set(results[0].keys())


def test_g_search_short_query(client):
    """Single-char query → 422 (min_length=2)."""
    r = client.get("/search?q=x")
    assert r.status_code == 422


def test_h_data_geojson_shell(client):
    """Empty village returns FeatureCollection shell."""
    r = client.get("/data?dist=1&taluk=9&hobli=3&vlg=46")
    assert r.status_code == 200
    body = r.json()
    assert body.get("type") == "FeatureCollection"
    assert isinstance(body["features"], list)


def test_i_search_returns_list(client):
    r = client.get("/search?q=30")
    assert r.status_code == 200
    results = r.json()
    assert isinstance(results, list)
    if results:
        required = {"survey_no", "village_name", "dist", "taluk", "hobli", "vlg"}
        assert required <= set(results[0].keys())


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
