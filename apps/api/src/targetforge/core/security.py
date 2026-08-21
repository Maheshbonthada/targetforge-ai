"""JWT validation against Microsoft Entra External ID. Identical pattern to
TrialReady's core/security.py — see that file's docstring for the JWKS
caching rationale.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt
from jose.exceptions import JOSEError

from targetforge.config import Settings, get_settings

logger = structlog.get_logger(__name__)
_bearer_scheme = HTTPBearer(auto_error=False)
_JWKS_CACHE_TTL_SECONDS = 3600


@dataclass(frozen=True)
class AuthenticatedUser:
    subject: str
    email: str | None
    display_name: str | None


class JwksCache:
    def __init__(self, issuer: str, ttl_seconds: int = _JWKS_CACHE_TTL_SECONDS) -> None:
        self._jwks_uri = f"{issuer.rstrip('/')}/discovery/v2.0/keys"
        self._ttl_seconds = ttl_seconds
        self._keys_by_kid: dict[str, dict] = {}
        self._fetched_at: float = 0.0

    async def get_key(self, kid: str) -> dict:
        if kid not in self._keys_by_kid or self._is_stale():
            await self._refresh()
        if kid not in self._keys_by_kid:
            raise JOSEError(f"Unknown key id {kid!r} — not present in issuer JWKS")
        return self._keys_by_kid[kid]

    def _is_stale(self) -> bool:
        return (time.monotonic() - self._fetched_at) > self._ttl_seconds

    async def _refresh(self) -> None:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(self._jwks_uri)
            response.raise_for_status()
        self._keys_by_kid = {key["kid"]: key for key in response.json()["keys"]}
        self._fetched_at = time.monotonic()


def get_jwks_cache(settings: Settings = Depends(get_settings)) -> JwksCache:
    return JwksCache(settings.entra_issuer)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    settings: Settings = Depends(get_settings),
    jwks_cache: JwksCache = Depends(get_jwks_cache),
) -> AuthenticatedUser:
    if settings.auth_disabled_for_local_dev:
        return AuthenticatedUser(subject="local-dev-user", email="dev@example.com", display_name="Local Dev")

    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    try:
        header = jwt.get_unverified_header(credentials.credentials)
        key = await jwks_cache.get_key(header["kid"])
        claims = jwt.decode(
            credentials.credentials,
            key,
            algorithms=[header.get("alg", "RS256")],
            audience=settings.entra_audience,
            issuer=settings.entra_issuer,
        )
    except (JOSEError, httpx.HTTPError, KeyError) as exc:
        logger.warning("auth.token_rejected", error=str(exc))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc

    return AuthenticatedUser(
        subject=claims["sub"],
        email=claims.get("email") or claims.get("preferred_username"),
        display_name=claims.get("name"),
    )
