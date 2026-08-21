"""Unit tests for `get_current_user` (core/security.py) — the function that
was written correctly but never actually wired into a router until this
audit (see api/routers/runs.py). Tested directly here, independent of the
full HTTP app, since exercising the 401 path through the app would require
either mutating the process-wide cached Settings singleton mid-suite or
fabricating a real signed JWT — both more machinery than the thing being
verified (the function's control flow) actually needs.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from targetforge.config import Settings
from targetforge.core.security import get_current_user


@pytest.mark.asyncio
async def test_auth_disabled_for_local_dev_bypasses_token_check():
    settings = Settings(auth_disabled_for_local_dev=True)
    user = await get_current_user(credentials=None, settings=settings, jwks_cache=None)
    assert user.subject == "local-dev-user"


@pytest.mark.asyncio
async def test_missing_bearer_token_is_rejected_when_auth_is_enabled():
    settings = Settings(auth_disabled_for_local_dev=False, entra_issuer="https://example.com/tenant")
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(credentials=None, settings=settings, jwks_cache=None)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_malformed_token_is_rejected_not_500ed():
    from fastapi.security import HTTPAuthorizationCredentials

    settings = Settings(auth_disabled_for_local_dev=False, entra_issuer="https://example.com/tenant")
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="not-a-real-jwt")
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(credentials=credentials, settings=settings, jwks_cache=None)
    assert exc_info.value.status_code == 401
