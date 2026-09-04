# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

from __future__ import annotations

import os

import httpx
from fastapi import Header, HTTPException
from jose import JWTError, jwt

_KC_URL = os.getenv("KEYCLOAK_URL", "http://keycloak:8080")
_KC_REALM = os.getenv("KEYCLOAK_REALM", "sat")
_JWKS_URI = f"{_KC_URL}/realms/{_KC_REALM}/protocol/openid-connect/certs"
_jwks_cache: dict | None = None


async def _get_jwks() -> dict:
    global _jwks_cache
    if _jwks_cache is None:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(_JWKS_URI, timeout=5)
                r.raise_for_status()
                _jwks_cache = r.json()
        except Exception as exc:
            raise HTTPException(status_code=401, detail=f"Auth service unreachable: {exc}") from exc
    return _jwks_cache


async def verify_token(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = authorization.removeprefix("Bearer ")
    try:
        jwks = await _get_jwks()
        payload = jwt.decode(
            token, jwks, algorithms=["RS256"], options={"verify_aud": False}
        )
        return payload
    except HTTPException:
        raise
    except JWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Token verification failed: {exc}") from exc
