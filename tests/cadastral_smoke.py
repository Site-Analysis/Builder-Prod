# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Cadastral service smoke tests — Phase 1B.

Covers:
  (a) /health → {status: ok, service: cadastral}
  (b) all land-record endpoints → 403 without flag
  (c) /districts → list[{code, name}] with flag
  (d) /taluks?dist=1 → list shape with flag
  (e) /hoblis?dist=1&taluk=9 → list shape with flag
  (f) /villages?dist=1&taluk=9&hobli=3 → list shape with flag
  (g) /search short query → 422
  (h) /data → GeoJSON FeatureCollection shell with flag
  (i) /search → list (empty OK, shape checked if survey_index exists)

Run: pytest tests/cadastral_smoke.py
Requires geopandas in the active venv: cd services/cadastral && pip install -r requirements.txt
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_SVC = Path(__file__).resolve().parents[1] / "services" / "cadastral"
if str(_SVC) in sys.path:
    sys.path.remove(str(_SVC))
sys.path.insert(0, str(_SVC))
sys.modules.pop("app", None)

import pytest

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
_HAS_DATA = bool(os.environ.get("CADASTRAL_DATA_DIR"))


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("FLAGS", _LAND_FLAG)
    sys.modules.pop("app", None)
    sys.modules.pop("app.main", None)
    from app.main import app
    return TestClient(app)


@pytest.fixture
def client_no_flags(monkeypatch):
    monkeypatch.setenv("FLAGS", "")
    sys.modules.pop("app", None)
    sys.modules.pop("app.main", None)
    from app.main import app
    return TestClient(app)


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
        "/data",
        "/search?q=30",
    ]:
        r = client_no_flags.get(path)
        assert r.status_code == 403, f"Expected 403 for {path}, got {r.status_code}"


def test_c_districts_shape(client):
    r = client.get("/districts")
    assert r.status_code == 200
    results = r.json()
    assert isinstance(results, list)
    if results:
        assert {"code", "name"} <= set(results[0].keys())


def test_d_taluks_shape(client):
    r = client.get("/taluks?dist=1")
    assert r.status_code == 200
    results = r.json()
    assert isinstance(results, list)
    if results:
        assert {"code", "name"} <= set(results[0].keys())


def test_e_hoblis_shape(client):
    r = client.get("/hoblis?dist=1&taluk=9")
    assert r.status_code == 200
    results = r.json()
    assert isinstance(results, list)
    if results:
        assert {"code", "name"} <= set(results[0].keys())


def test_f_villages_shape(client):
    r = client.get("/villages?dist=1&taluk=9&hobli=3")
    assert r.status_code == 200
    results = r.json()
    assert isinstance(results, list)
    if results:
        assert {"code", "name"} <= set(results[0].keys())


def test_g_search_short_query(client):
    """Single-char query → 422 (min_length=2)."""
    r = client.get("/search?q=x")
    assert r.status_code == 422


@pytest.mark.skipif(not _HAS_DATA, reason="CADASTRAL_DATA_DIR not set")
def test_h_data_geojson_shell(client):
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
