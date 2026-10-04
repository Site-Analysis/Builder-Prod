# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Cadastral service smoke tests — Phase 1B/1D.

Covers:
  (a) /health → {status: ok, service: cadastral}
  (b) all land-record endpoints → 403 without flag (auth bypassed)
  (c) /districts → list[{code, name}] with flag
  (d) /taluks?dist=1 → list shape with flag
  (e) /hoblis?dist=1&taluk=9 → list shape with flag
  (f) /villages?dist=1&taluk=9&hobli=3 → list shape with flag
  (g) /search short query → 422
  (h) /data → GeoJSON FeatureCollection shell with flag (skipped without CADASTRAL_DATA_DIR)
  (i) /search → list (empty OK; shape checked if survey_index populated)
  (j) /village-search short query → 422
  (k) /village-search → list (empty OK without parquet data; shape checked if populated)
  (l) /nearby → GeoJSON FeatureCollection (empty OK without LGD data)
  (n) auth: no token, wrong iss, wrong azp, expired, ID token as bearer → 401; valid → passes
  (o) DEV_BYPASS_AUTH refuses to start unless APP_ENV=local
  (p) /openapi.json and /docs off unless ENABLE_API_DOCS=1

Run: pytest tests/cadastral_smoke.py
Requires geopandas: cd services/cadastral && pip install -r requirements.txt
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

# Dummy payload returned by overridden verify_token — satisfies FastAPI dependency type.
_DUMMY_PAYLOAD = {"sub": "test-user", "preferred_username": "smoke-test"}


_KC_URL = "https://kc.test/auth"
_KC_REALM = "TestRealm"
_KC_CLIENT = "sat-builder"
_ISSUER = f"{_KC_URL}/realms/{_KC_REALM}"


def _import_app(monkeypatch, tmp_path, flags: str, **env):
    """Fresh import of app.main with the given env (Keycloak settings set by default)."""
    monkeypatch.setenv("FLAGS", flags)
    monkeypatch.setenv("SURVEY_INDEX_DB", str(tmp_path / "survey_index.db"))
    base = {
        "KEYCLOAK_URL": _KC_URL,
        "KEYCLOAK_REALM": _KC_REALM,
        "KEYCLOAK_CLIENT_ID": _KC_CLIENT,
    }
    for k in ("DEV_BYPASS_AUTH", "APP_ENV", "ENABLE_API_DOCS", "KEYCLOAK_ISSUER",
              "KEYCLOAK_JWKS_URL"):
        monkeypatch.delenv(k, raising=False)
    for k, v in {**base, **env}.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    for m in [k for k in sys.modules if k == "app" or k.startswith("app.")]:
        sys.modules.pop(m, None)
    from app.main import app

    return app


def _make_client(monkeypatch, tmp_path, flags: str):
    """Build a TestClient with auth overridden and SURVEY_INDEX_DB in a writable tmpdir."""
    app = _import_app(monkeypatch, tmp_path, flags)
    from app.auth import verify_token

    app.dependency_overrides[verify_token] = lambda: _DUMMY_PAYLOAD
    client = TestClient(app)
    return client, app


@pytest.fixture
def client(monkeypatch, tmp_path):
    c, app = _make_client(monkeypatch, tmp_path, _LAND_FLAG)
    yield c
    app.dependency_overrides.clear()


@pytest.fixture
def client_no_flags(monkeypatch, tmp_path):
    c, app = _make_client(monkeypatch, tmp_path, "")
    yield c
    app.dependency_overrides.clear()


def test_a_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "cadastral"


def test_b_flag_guard(client_no_flags):
    """All gated endpoints return 403 without flag (auth bypassed so flag is the only gate)."""
    for path in [
        "/districts",
        "/taluks?dist=1",
        "/hoblis?dist=1&taluk=9",
        "/villages?dist=1&taluk=9&hobli=3",
        "/data",
        "/search?q=30",
        "/village-search?q=Ha",
        "/nearby?lat=12.9&lng=77.5",
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


def test_j_village_search_short_query(client):
    """Single-char query → 422 (min_length=2)."""
    r = client.get("/village-search?q=H")
    assert r.status_code == 422


def test_k_village_search_returns_list(client):
    r = client.get("/village-search?q=Ha")
    assert r.status_code == 200
    results = r.json()
    assert isinstance(results, list)
    if results:
        required = {"village_name", "dist", "taluk", "hobli", "vlg"}
        assert required <= set(results[0].keys())


def test_l_nearby_returns_geojson(client):
    """Nearby always returns valid GeoJSON; features may be empty without LGD data."""
    r = client.get("/nearby?lat=12.9716&lng=77.5946&radius_km=5")
    assert r.status_code == 200
    body = r.json()
    assert body.get("type") == "FeatureCollection"
    assert isinstance(body.get("features"), list)


@pytest.mark.skipif(not os.environ.get("RTC_SMOKE"), reason="set RTC_SMOKE=1 to hit live eChhawadi")
def test_m_rtc_shape(client):
    r = client.get("/rtc?dist=1&taluk=9&hobli=3&vlg=46&village_code=603735")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body.get("owners"), list)
    assert isinstance(body.get("mutations"), list)


# ─── auth (n-p) ─────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def _rsa():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jose import jwk

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    public_jwk = {**jwk.construct(pub, "RS256").to_dict(), "kid": "k1", "use": "sig"}
    return pem, {"keys": [public_jwk]}


def _token(pem, **claims):
    import time as _t

    from jose import jwt

    now = int(_t.time())
    body = {
        "iss": _ISSUER,
        "sub": "user-1",
        "azp": _KC_CLIENT,
        "typ": "Bearer",
        "iat": now,
        "exp": now + 300,
        **claims,
    }
    return jwt.encode(body, pem, algorithm="RS256", headers={"kid": "k1"})


@pytest.fixture
def real_auth(monkeypatch, tmp_path, _rsa):
    """App with the real verify_token; JWKS pre-loaded so no network is used."""
    import time as _t

    app = _import_app(monkeypatch, tmp_path, _LAND_FLAG)
    from app import auth

    monkeypatch.setattr(auth, "_jwks_cache", _rsa[1])
    monkeypatch.setattr(auth, "_jwks_fetched_at", _t.monotonic())
    return TestClient(app), _rsa[0]


def test_n_auth_rejects_bad_tokens(real_auth):
    import time as _t

    c, pem = real_auth
    now = int(_t.time())
    cases = {
        "no token": None,
        "wrong iss": _token(pem, iss="https://evil.test/realms/x"),
        "wrong azp": _token(pem, azp="other-client"),
        "ID token as bearer": _token(pem, typ="ID"),
        "expired": _token(pem, iat=now - 600, exp=now - 300),
        "no exp": _token(pem, exp=None),
    }
    for name, tok in cases.items():
        h = {"Authorization": f"Bearer {tok}"} if tok else {}
        r = c.get("/districts", headers=h)
        assert r.status_code == 401, (name, r.status_code, r.text)


def test_n_auth_accepts_valid_access_token(real_auth):
    c, pem = real_auth
    r = c.get("/districts", headers={"Authorization": f"Bearer {_token(pem)}"})
    assert r.status_code != 401, r.text
    assert c.get("/health").status_code == 200  # health needs no token


def test_o_bypass_needs_app_env_local(monkeypatch, tmp_path):
    for env in (None, "production", "uat", "staging"):
        with pytest.raises(RuntimeError, match="APP_ENV=local"):
            _import_app(
                monkeypatch, tmp_path, _LAND_FLAG, DEV_BYPASS_AUTH="1", APP_ENV=env
            )
    app = _import_app(
        monkeypatch, tmp_path, _LAND_FLAG, DEV_BYPASS_AUTH="1", APP_ENV="local"
    )
    assert TestClient(app).get("/districts").status_code != 401


def test_o_missing_keycloak_config_refuses_start(monkeypatch, tmp_path):
    with pytest.raises(RuntimeError, match="KEYCLOAK_CLIENT_ID"):
        _import_app(monkeypatch, tmp_path, _LAND_FLAG, KEYCLOAK_CLIENT_ID=None)


def test_p_docs_off_by_default(monkeypatch, tmp_path):
    c = TestClient(_import_app(monkeypatch, tmp_path, _LAND_FLAG))
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert c.get(path).status_code == 404, path
    c = TestClient(_import_app(monkeypatch, tmp_path, _LAND_FLAG, ENABLE_API_DOCS="1"))
    assert c.get("/openapi.json").status_code == 200


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
