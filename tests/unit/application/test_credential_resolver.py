"""Credential resolver failure normalization tests."""

from __future__ import annotations

from uuid import UUID

import pytest

import ops.application.credential_resolver as resolver_module
from ops.application.credential_resolver import CpsResolutionError, CredentialResolver
from ops.config import Settings


class _Response:
    status_code = 503
    content = b'{"error":{"code":"CREDENTIAL_KEY_UNAVAILABLE"}}'

    def json(self) -> dict[str, object]:
        return {"error": {"code": "CREDENTIAL_KEY_UNAVAILABLE"}}


class _Client:
    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()


@pytest.mark.asyncio
async def test_resolver_preserves_safe_cps_error_code_for_retryable_5xx(monkeypatch) -> None:
    monkeypatch.setattr(resolver_module.httpx, "AsyncClient", lambda **_kwargs: _Client())
    resolver = CredentialResolver(Settings(environment="test", _env_file=None))

    with pytest.raises(CpsResolutionError) as raised:
        await resolver.resolve(
            UUID("66666666-6666-4666-8666-666666666666"),
            UUID("55555555-5555-4555-8555-555555555555"),
        )

    assert raised.value.code == "CREDENTIAL_KEY_UNAVAILABLE"
    assert raised.value.retryable is True
