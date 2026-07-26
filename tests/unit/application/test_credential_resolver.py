"""Credential resolver failure normalization tests."""

from __future__ import annotations

from uuid import UUID

import httpx
import pytest

import ops.application.credential_resolver as resolver_module
from ops.application.credential_resolver import CpsResolutionError, CredentialResolver
from ops.config import Settings

PROVIDER_ID = UUID("44444444-4444-4444-8444-444444444444")
CREDENTIAL_ID = UUID("66666666-6666-4666-8666-666666666666")
CONNECTION_ID = UUID("55555555-5555-4555-8555-555555555555")

RESOLUTION_PAYLOAD = {
    "schema_version": "1.0",
    "auth_url": "https://keystone.example/v3",
    "username": "ops-user",
    "password": "secret",  # pragma: allowlist secret
    "user_domain_name": "Default",
    "project_name": "demo",
    "project_domain_name": "Default",
    "region_name": "RegionOne",
    "interface": "public",
    "verify_tls": True,
}


class _Response:
    def __init__(
        self,
        *,
        status_code: int = 200,
        content: bytes = b"",
        payload: dict[str, object] | None = None,
    ) -> None:
        self.status_code = status_code
        self.content = content or json_bytes(payload)
        self._payload = payload or {}

    def json(self) -> dict[str, object]:
        return self._payload


def json_bytes(payload: dict[str, object] | None) -> bytes:
    import json

    return json.dumps(payload or {}).encode()


class _Client:
    def __init__(self, *, response: _Response | None = None, on_get=None) -> None:
        self._response = response or _Response(payload=RESOLUTION_PAYLOAD)
        self._on_get = on_get
        self.last_url: str | None = None
        self.last_params: dict[str, str] | None = None

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, url: str, *, params: dict[str, str] | None = None) -> _Response:
        self.last_url = url
        self.last_params = params
        if self._on_get is not None:
            return self._on_get(url, params)
        return self._response


def _patch_client(monkeypatch, client: _Client) -> None:
    monkeypatch.setattr(
        resolver_module.httpx,
        "AsyncClient",
        lambda **_kwargs: client,
    )


@pytest.mark.asyncio
async def test_resolver_preserves_safe_cps_error_code_for_retryable_5xx(monkeypatch) -> None:
    client = _Client(
        response=_Response(
            status_code=503,
            payload={"error": {"code": "CREDENTIAL_KEY_UNAVAILABLE"}},
        )
    )
    _patch_client(monkeypatch, client)
    resolver = CredentialResolver(
        Settings(environment="test", _env_file=None, cps_base_url="http://cps")
    )

    with pytest.raises(CpsResolutionError) as raised:
        await resolver.resolve(CREDENTIAL_ID, CONNECTION_ID)

    assert raised.value.code == "CREDENTIAL_KEY_UNAVAILABLE"
    assert raised.value.retryable is True


@pytest.mark.asyncio
async def test_resolve_by_provider_id_uses_provider_resolution_url(monkeypatch) -> None:
    client = _Client()
    _patch_client(monkeypatch, client)
    resolver = CredentialResolver(
        Settings(environment="test", _env_file=None, cps_base_url="http://cps")
    )

    await resolver.resolve_by_provider_id(PROVIDER_ID)

    assert client.last_url == f"http://cps/internal/v1/providers/{PROVIDER_ID}/resolution"
    assert client.last_params is None


@pytest.mark.asyncio
async def test_resolve_by_provider_id_maps_invalid_provider(monkeypatch) -> None:
    client = _Client(
        response=_Response(
            status_code=404,
            payload={"error": {"code": "PROVIDER_NOT_FOUND"}},
        )
    )
    _patch_client(monkeypatch, client)
    resolver = CredentialResolver(
        Settings(environment="test", _env_file=None, cps_base_url="http://cps")
    )

    with pytest.raises(CpsResolutionError) as raised:
        await resolver.resolve_by_provider_id(PROVIDER_ID)

    assert raised.value.code == "PROVIDER_NOT_FOUND"
    assert raised.value.retryable is False


@pytest.mark.asyncio
async def test_resolve_by_provider_id_maps_cps_unavailable_on_network_error(monkeypatch) -> None:
    class _FailingClient:
        async def __aenter__(self) -> _FailingClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, *_args: object, **_kwargs: object) -> _Response:
            raise httpx.NetworkError("connection refused")

    monkeypatch.setattr(resolver_module.httpx, "AsyncClient", lambda **_kwargs: _FailingClient())
    resolver = CredentialResolver(
        Settings(environment="test", _env_file=None, cps_base_url="http://cps")
    )

    with pytest.raises(CpsResolutionError) as raised:
        await resolver.resolve_by_provider_id(PROVIDER_ID)

    assert raised.value.code == "CPS_UNAVAILABLE"
    assert raised.value.retryable is True
