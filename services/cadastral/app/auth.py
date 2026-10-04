# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Keycloak access-token check for every data route.

A request passes only with an RS256 access token whose signature matches the realm's
JWKS, whose `iss` is KEYCLOAK_ISSUER, which has not expired, whose `typ` is "Bearer"
(an ID token is "ID") and whose `azp` is KEYCLOAK_CLIENT_ID. Anything else is 401.

Config (read at start-up; check_config() refuses to start on a bad setup):
  KEYCLOAK_URL        public Keycloak base incl. any path prefix, e.g. https://uat.qnit.in/auth
  KEYCLOAK_REALM      realm name
  KEYCLOAK_CLIENT_ID  the builder client (checked against azp)
  KEYCLOAK_ISSUER     optional; default {KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}
  KEYCLOAK_JWKS_URL   optional; e.g. the internal http://keycloak-uat:8080/auth/... URL
  DEV_BYPASS_AUTH     skips all checks; allowed only with APP_ENV=local
"""

from __future__ import annotations

import os
import time

import httpx
from fastapi import Header, HTTPException
from jose import JWTError, jwt

_TRUTHY = ("1", "true", "yes")


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


_KC_URL = _env("KEYCLOAK_URL").rstrip("/")
_KC_REALM = _env("KEYCLOAK_REALM")
_CLIENT_ID = _env("KEYCLOAK_CLIENT_ID")
_ISSUER = _env("KEYCLOAK_ISSUER").rstrip("/") or (
    f"{_KC_URL}/realms/{_KC_REALM}" if _KC_URL and _KC_REALM else ""
)
_JWKS_URI = _env("KEYCLOAK_JWKS_URL") or (
    f"{_ISSUER}/protocol/openid-connect/certs" if _ISSUER else ""
)
_DEV_BYPASS = _env("DEV_BYPASS_AUTH").lower() in _TRUTHY
_APP_ENV = _env("APP_ENV").lower()

# an unknown kid triggers a JWKS refresh at most this often (key rotation, not a DoS lever)
_JWKS_REFRESH_MIN_S = 30.0
_jwks_cache: dict | None = None
_jwks_fetched_at = 0.0


def check_config() -> None:
    """Fail closed at start-up: bypass only on APP_ENV=local; otherwise the Keycloak
    settings must all be present."""
    if _DEV_BYPASS:
        if _APP_ENV != "local":
            raise RuntimeError(
                "DEV_BYPASS_AUTH is set but APP_ENV is "
                f"{_APP_ENV or 'unset'!r}; it is allowed only with APP_ENV=local. "
                "Refusing to start."
            )
        return
    missing = [
        n
        for n, v in (
            ("KEYCLOAK_URL", _KC_URL),
            ("KEYCLOAK_REALM", _KC_REALM),
            ("KEYCLOAK_CLIENT_ID", _CLIENT_ID),
        )
        if not v
    ]
    if missing:
        raise RuntimeError(
            "Missing required environment variable(s): " + ", ".join(missing)
        )


async def _fetch_jwks() -> dict:
    global _jwks_cache, _jwks_fetched_at
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(_JWKS_URI, timeout=5)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        raise HTTPException(
            status_code=401, detail=f"Auth service unreachable: {exc}"
        ) from exc
    if not data.get("keys"):
        raise HTTPException(status_code=401, detail="Empty JWKS from Keycloak")
    _jwks_cache, _jwks_fetched_at = data, time.monotonic()
    return data


async def _key_for(kid: str | None) -> dict:
    """The JWKS key with this kid; the JWKS is cached and refreshed on an unknown kid."""
    jwks = _jwks_cache or await _fetch_jwks()
    for k in jwks.get("keys", []):
        if k.get("kid") == kid:
            return k
    if time.monotonic() - _jwks_fetched_at >= _JWKS_REFRESH_MIN_S:
        jwks = await _fetch_jwks()
        for k in jwks.get("keys", []):
            if k.get("kid") == kid:
                return k
    raise HTTPException(status_code=401, detail="Invalid token: unknown signing key")


async def verify_token(authorization: str | None = Header(default=None)) -> dict:
    if _DEV_BYPASS:
        return {"sub": "dev", "preferred_username": "dev"}
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256":
            raise HTTPException(status_code=401, detail="Invalid token: algorithm")
        key = await _key_for(header.get("kid"))
        payload = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=_ISSUER,
            # Keycloak access tokens carry aud="account"; the client is checked via azp
            options={"verify_aud": False, "require_exp": True, "require_iss": True},
        )
    except HTTPException:
        raise
    except JWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}") from exc
    except Exception as exc:
        raise HTTPException(
            status_code=401, detail=f"Token verification failed: {exc}"
        ) from exc
    if payload.get("typ") != "Bearer":
        raise HTTPException(
            status_code=401, detail="Invalid token: not an access token"
        )
    if payload.get("azp") != _CLIENT_ID:
        raise HTTPException(status_code=401, detail="Invalid token: wrong client")
    return payload
