"""Production connection validation handler tests."""

from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import UUID

import pytest

import ops.application.handlers.connection_validate as handler_module
from ops.application.handlers.connection_validate import connection_validate
from ops.config import Settings
from ops.contracts.messages.envelope import MessageEnvelope
from ops.contracts.messages.types import OPERATION_COMPLETED, OPERATION_FAILED, OPERATION_PROGRESS
from ops.contracts.validation import CapabilityDocument
from ops.messaging.consumer import HandlerFailedResult, HandlerRetryableError, HandlerSuccess


def _resolution_payload() -> dict[str, object]:
    return {
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


def _validation_command(*, include_credential_reference: bool = False) -> MessageEnvelope:
    payload: dict[str, object] = {
        "message_id": "11111111-1111-4111-8111-111111111111",
        "message_type": "openstack.connection.validate",
        "schema_version": "1.0",
        "occurred_at": "2026-07-17T00:00:00Z",
        "correlation_id": "22222222-2222-4222-8222-222222222222",
        "operation_id": "33333333-3333-4333-8333-333333333333",
        "provider_id": "44444444-4444-4444-8444-444444444444",
        "provider_connection_id": "55555555-5555-4555-8555-555555555555",
        "trace_context": {},
        "payload": {"validation_mode": "SAFE_READ_ONLY"},
    }
    if include_credential_reference:
        payload["credential_reference"] = "66666666-6666-4666-8666-666666666666"
    return MessageEnvelope.model_validate(payload)


def _capabilities() -> CapabilityDocument:
    return CapabilityDocument.model_validate(
        {
            "schema_version": "1.0",
            "services": {
                "identity": {"available": True},
                "compute": {"available": True},
                "network": {"available": True},
                "image": {"available": True},
                "block_storage": {"available": False},
            },
            "features": {
                "connection.authenticate": {"supported": True},
                "service.identity": {"supported": True},
                "service.compute": {"supported": True},
                "service.network": {"supported": True},
                "service.image": {"supported": True},
                "service.block_storage": {"supported": False},
            },
        }
    )


@pytest.mark.asyncio
async def test_connection_validate_succeeds_without_credential_reference(monkeypatch) -> None:
    resolved_provider_id: UUID | None = None

    class FakeResolver:
        def __init__(self, _settings):
            pass

        async def resolve_by_provider_id(self, provider_id: UUID):
            nonlocal resolved_provider_id
            resolved_provider_id = provider_id
            return SimpleNamespace(**_resolution_payload())

    @contextmanager
    def fake_connection(_resolution, _settings):
        yield SimpleNamespace()

    monkeypatch.setattr(handler_module, "CredentialResolver", FakeResolver)
    monkeypatch.setattr(handler_module, "openstack_connection", fake_connection)
    monkeypatch.setattr(
        handler_module,
        "discover_capabilities",
        lambda _conn: _capabilities(),
    )

    outcome = await connection_validate(
        _validation_command(include_credential_reference=False),
        None,
        "openstack.connection.validate",
        settings=Settings(environment="test", _env_file=None),
    )

    assert isinstance(outcome, HandlerSuccess)
    assert resolved_provider_id == UUID("44444444-4444-4444-8444-444444444444")
    routing_keys = [routing_key for routing_key, _body in outcome.result_messages]
    assert routing_keys == [
        OPERATION_PROGRESS,
        OPERATION_PROGRESS,
        OPERATION_COMPLETED,
    ]
    completed_body = json.loads(outcome.result_messages[-1][1])
    assert completed_body["payload"]["result"]["status"] == "VALID"
    assert "password" not in json.dumps(completed_body)


@pytest.mark.asyncio
async def test_connection_validate_maps_provider_not_found(monkeypatch) -> None:
    from ops.application.credential_resolver import CpsResolutionError

    class FakeResolver:
        def __init__(self, _settings):
            pass

        async def resolve_by_provider_id(self, _provider_id: UUID):
            raise CpsResolutionError("PROVIDER_NOT_FOUND", retryable=False)

    monkeypatch.setattr(handler_module, "CredentialResolver", FakeResolver)

    outcome = await connection_validate(
        _validation_command(include_credential_reference=False),
        None,
        "openstack.connection.validate",
        settings=Settings(environment="test", _env_file=None),
    )

    assert isinstance(outcome, HandlerFailedResult)
    assert outcome.result_routing_key == OPERATION_FAILED
    failed = json.loads(outcome.result_body)
    error = failed["payload"]["error"]
    assert error["code"] == "PROVIDER_NOT_FOUND"
    assert error["retryable"] is False
    assert "password" not in json.dumps(failed)


@pytest.mark.asyncio
async def test_connection_validate_retries_when_cps_unavailable(monkeypatch) -> None:
    from ops.application.credential_resolver import CpsResolutionError

    class FakeResolver:
        def __init__(self, _settings):
            pass

        async def resolve_by_provider_id(self, _provider_id: UUID):
            raise CpsResolutionError("CPS_UNAVAILABLE", retryable=True)

    monkeypatch.setattr(handler_module, "CredentialResolver", FakeResolver)

    outcome = await connection_validate(
        _validation_command(include_credential_reference=False),
        None,
        "openstack.connection.validate",
        settings=Settings(environment="test", _env_file=None),
    )

    assert isinstance(outcome, HandlerRetryableError)
    assert outcome.retry_reason == "CPS_UNAVAILABLE"
    assert outcome.error is not None
    assert outcome.error.code == "CPS_UNAVAILABLE"
    assert outcome.error.retryable is True
