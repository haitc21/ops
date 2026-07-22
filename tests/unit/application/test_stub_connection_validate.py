"""Unit tests for stub connection validate handler."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError
from tests.unit.messaging.fakes import fresh_delivery_headers

from ops.application.handlers.stub_connection_validate import (
    build_completed_event,
    build_failed_event,
    build_progress_event,
    stub_connection_validate,
)
from ops.contracts.messages.envelope import MessageEnvelope
from ops.contracts.messages.types import (
    OPERATION_COMPLETED,
    OPERATION_FAILED,
    OPERATION_PROGRESS,
)
from ops.identifiers import new_uuid7
from ops.messaging.consumer import HandlerSuccess
from ops.messaging.retry import parse_command_delivery_metadata

COMMAND_FIXTURE = json.loads(
    Path("src/ops/contracts/fixtures/commands/connection_validate.json").read_text(
        encoding="utf-8",
    )
)
COMPLETED_FIXTURE = json.loads(
    Path("src/ops/contracts/fixtures/events/operation_completed.json").read_text(
        encoding="utf-8",
    )
)
FAILED_FIXTURE = json.loads(
    Path("src/ops/contracts/fixtures/events/operation_failed.json").read_text(
        encoding="utf-8",
    )
)

EventBuilder = Callable[..., MessageEnvelope]

_BUILDER_CASES = (
    pytest.param(
        build_progress_event,
        OPERATION_PROGRESS,
        7,
        datetime(2026, 7, 17, 0, 0, 1, tzinfo=UTC),
        id="progress",
    ),
    pytest.param(
        build_completed_event,
        OPERATION_COMPLETED,
        8,
        datetime(2026, 7, 17, 0, 0, 2, tzinfo=UTC),
        id="completed",
    ),
    pytest.param(
        build_failed_event,
        OPERATION_FAILED,
        9,
        datetime(2026, 7, 17, 0, 0, 3, tzinfo=UTC),
        id="failed",
    ),
)


def test_new_uuid7_is_version_7() -> None:
    assert new_uuid7().version == 7


def test_new_uuid7_ids_are_monotonic() -> None:
    first = new_uuid7()
    second = new_uuid7()
    assert second > first


@pytest.mark.parametrize(
    ("builder", "message_type", "message_id_int", "fixed_now"),
    _BUILDER_CASES,
)
def test_build_operation_event_preserves_command_ids(
    builder: EventBuilder,
    message_type: str,
    message_id_int: int,
    fixed_now: datetime,
) -> None:
    command = MessageEnvelope.model_validate(COMMAND_FIXTURE)
    event = builder(command, now=fixed_now, new_message_id=UUID(int=message_id_int))
    assert event.correlation_id == command.correlation_id
    assert event.causation_id == command.message_id
    assert event.operation_id == command.operation_id
    assert event.provider_id == command.provider_id
    assert event.provider_connection_id == command.provider_connection_id
    assert event.schema_version == "1.0"
    assert event.message_type == message_type
    assert event.idempotency_key is None


@pytest.mark.parametrize(
    ("builder", "_message_type", "_message_id_int", "fixed_now"),
    _BUILDER_CASES,
)
def test_build_operation_event_uses_uuidv7_and_utc(
    builder: EventBuilder,
    _message_type: str,
    _message_id_int: int,
    fixed_now: datetime,
) -> None:
    command = MessageEnvelope.model_validate(COMMAND_FIXTURE)
    message_id = new_uuid7()
    event = builder(command, now=fixed_now, new_message_id=message_id)
    assert event.message_id == message_id
    assert event.message_id.version == 7
    assert event.occurred_at == fixed_now
    assert event.occurred_at.tzinfo == UTC


@pytest.mark.parametrize(
    "builder", [build_progress_event, build_completed_event, build_failed_event]
)
def test_build_operation_event_omits_credential_reference(builder: EventBuilder) -> None:
    command = MessageEnvelope.model_validate(COMMAND_FIXTURE)
    event = builder(command)
    dumped = event.model_dump(mode="json", exclude_none=True)
    assert "credential_reference" not in dumped


def _synthetic_leak_marker() -> str:
    return "must-not-" + "leak-" + "trace"


@pytest.mark.parametrize(
    "builder", [build_progress_event, build_completed_event, build_failed_event]
)
def test_build_operation_event_redacts_nested_secret_trace_context(
    builder: EventBuilder,
) -> None:
    command = MessageEnvelope.model_validate(COMMAND_FIXTURE)
    leak = _synthetic_leak_marker()
    command.trace_context = {
        "traceparent": "00-abc-def-01",
        "baggage": {"password": leak, "route": "alpha"},
        "request": {"Authorization": "Bearer " + leak},
    }
    event = builder(command)
    body = json.dumps(event.model_dump(mode="json"))
    assert event.trace_context["baggage"]["password"] == "[REDACTED]"
    assert event.trace_context["request"]["Authorization"] == "[REDACTED]"
    assert leak not in body
    assert event.trace_context["traceparent"] == "00-abc-def-01"


@pytest.mark.parametrize(
    "builder", [build_progress_event, build_completed_event, build_failed_event]
)
def test_build_operation_event_copies_trace_context_without_alias(
    builder: EventBuilder,
) -> None:
    command = MessageEnvelope.model_validate(COMMAND_FIXTURE)
    command.trace_context["traceparent"] = "00-abc-def-01"
    event = builder(command)
    event.trace_context["traceparent"] = "mutated"
    assert command.trace_context["traceparent"] == "00-abc-def-01"


@pytest.mark.parametrize(
    ("builder", "message_type", "_message_id_int", "_fixed_now"),
    _BUILDER_CASES,
)
def test_serialized_operation_event_validates_as_message_envelope(
    builder: EventBuilder,
    message_type: str,
    _message_id_int: int,
    _fixed_now: datetime,
) -> None:
    command = MessageEnvelope.model_validate(COMMAND_FIXTURE)
    event = builder(command)
    raw = json.loads(json.dumps(event.model_dump(mode="json")))
    validated = MessageEnvelope.model_validate(raw)
    assert validated.message_type == message_type


def test_build_progress_event_deep_copies_nested_trace_context() -> None:
    command = MessageEnvelope.model_validate(COMMAND_FIXTURE)
    command.trace_context["baggage"] = {"route": "alpha"}
    event = build_progress_event(command)
    nested = event.trace_context["baggage"]
    assert isinstance(nested, dict)
    nested["route"] = "mutated"
    assert command.trace_context["baggage"]["route"] == "alpha"


def test_build_progress_event_does_not_claim_provider_valid() -> None:
    command = MessageEnvelope.model_validate(COMMAND_FIXTURE)
    event = build_progress_event(command)
    assert event.payload["message"] == "command accepted for dispatch validation"


def test_serialized_event_excludes_sensitive_command_payload() -> None:
    command = MessageEnvelope.model_validate(COMMAND_FIXTURE)
    event = build_progress_event(command)
    body = json.dumps(event.model_dump(mode="json"))
    lowered = body.lower()
    for forbidden in ("auth_url", "password", "token", "authorization", "user_data"):
        assert forbidden not in lowered


def test_build_completed_event_matches_canonical_payload_shape() -> None:
    command = MessageEnvelope.model_validate(COMMAND_FIXTURE)
    event = build_completed_event(command)
    assert event.payload == COMPLETED_FIXTURE["payload"]


def test_build_completed_event_isolates_default_from_mutation() -> None:
    command = MessageEnvelope.model_validate(COMMAND_FIXTURE)
    first = build_completed_event(command)
    first.payload["result"]["capabilities"]["services"]["compute"]["available"] = False
    second = build_completed_event(command)
    assert second.payload["result"]["capabilities"]["services"]["compute"]["available"] is True


def test_build_completed_event_isolates_caller_result_from_mutation() -> None:
    command = MessageEnvelope.model_validate(COMMAND_FIXTURE)
    caller_result: dict[str, Any] = {
        "status": "VALID",
        "capabilities": {
            "schema_version": "1.0",
            "services": {
                "identity": {"available": True},
                "compute": {"available": True},
                "network": {"available": True},
                "image": {"available": True},
                "block_storage": {"available": True},
            },
            "features": {
                "connection.authenticate": {"supported": True},
                "service.identity": {"supported": True},
                "service.compute": {"supported": True},
                "service.network": {"supported": True},
                "service.image": {"supported": True},
                "service.block_storage": {"supported": True},
            },
        },
    }
    first = build_completed_event(command, result=caller_result)
    first.payload["result"]["capabilities"]["services"]["compute"]["available"] = False
    assert caller_result["capabilities"]["services"]["compute"]["available"] is True
    second = build_completed_event(command, result=caller_result)
    assert second.payload["result"]["capabilities"]["services"]["compute"]["available"] is True


def test_build_failed_event_matches_canonical_payload_shape() -> None:
    command = MessageEnvelope.model_validate(COMMAND_FIXTURE)
    fixed_now = datetime(2026, 7, 17, 0, 0, 3, tzinfo=UTC)
    event = build_failed_event(command, now=fixed_now)
    assert event.payload == FAILED_FIXTURE["payload"]


def test_build_failed_event_caller_error_without_occurred_at_uses_event_timestamp() -> None:
    command = MessageEnvelope.model_validate(COMMAND_FIXTURE)
    fixed_now = datetime(2026, 7, 17, 0, 0, 3, tzinfo=UTC)
    caller_error = {
        "code": "PROVIDER_AUTHENTICATION_FAILED",
        "message": "OpenStack authentication failed",
        "category": "AUTHENTICATION",
        "retryable": False,
        "provider": "OPENSTACK",
        "provider_service": "identity",
        "provider_request_id": "req-synthetic",
        "details": {"reason": "invalid credentials"},
    }
    event = build_failed_event(command, error=caller_error, now=fixed_now)
    assert event.occurred_at == fixed_now
    assert event.payload["error"]["occurred_at"] == "2026-07-17T00:00:03Z"


def test_build_failed_event_caller_error_preserves_supplied_occurred_at() -> None:
    command = MessageEnvelope.model_validate(COMMAND_FIXTURE)
    fixed_now = datetime(2026, 7, 17, 0, 0, 3, tzinfo=UTC)
    caller_occurred_at = "2026-07-16T12:00:00Z"
    caller_error = {
        "code": "PROVIDER_AUTHENTICATION_FAILED",
        "message": "OpenStack authentication failed",
        "category": "AUTHENTICATION",
        "retryable": False,
        "provider": "OPENSTACK",
        "provider_service": "identity",
        "provider_request_id": "req-synthetic",
        "details": {"reason": "invalid credentials"},
        "occurred_at": caller_occurred_at,
    }
    event = build_failed_event(command, error=caller_error, now=fixed_now)
    assert event.payload["error"]["occurred_at"] == caller_occurred_at


def test_build_failed_event_isolates_default_from_mutation() -> None:
    command = MessageEnvelope.model_validate(COMMAND_FIXTURE)
    fixed_now = datetime(2026, 7, 17, 0, 0, 3, tzinfo=UTC)
    first = build_failed_event(command, now=fixed_now)
    first.payload["error"]["details"]["mutated"] = True
    second = build_failed_event(command, now=fixed_now)
    assert "mutated" not in second.payload["error"]["details"]


def test_build_failed_event_isolates_caller_error_from_mutation() -> None:
    command = MessageEnvelope.model_validate(COMMAND_FIXTURE)
    caller_error: dict[str, Any] = {
        "code": "PROVIDER_AUTHENTICATION_FAILED",
        "message": "OpenStack authentication failed",
        "category": "AUTHENTICATION",
        "retryable": False,
        "provider": "OPENSTACK",
        "provider_service": "identity",
        "provider_request_id": "req-synthetic",
        "details": {"reason": "invalid credentials"},
        "occurred_at": "2026-07-17T00:00:03Z",
    }
    first = build_failed_event(command, error=caller_error)
    first.payload["error"]["details"]["reason"] = "mutated"
    assert caller_error["details"]["reason"] == "invalid credentials"
    second = build_failed_event(command, error=caller_error)
    assert second.payload["error"]["details"]["reason"] == "invalid credentials"


def test_build_failed_event_rejects_invalid_common_error() -> None:
    command = MessageEnvelope.model_validate(COMMAND_FIXTURE)
    with pytest.raises(ValidationError):
        build_failed_event(
            command,
            error={
                "code": "INVALID",
                "message": "bad category",
                "category": "NOT_A_REAL_CATEGORY",
                "retryable": False,
            },
        )


@pytest.mark.asyncio
async def test_stub_connection_validate_returns_progress_only() -> None:
    command = MessageEnvelope.model_validate(COMMAND_FIXTURE)
    outcome = await stub_connection_validate(
        command,
        parse_command_delivery_metadata(fresh_delivery_headers()),
        "openstack.connection.validate",
    )
    assert isinstance(outcome, HandlerSuccess)
    assert outcome.result_routing_key == OPERATION_PROGRESS
    event = MessageEnvelope.model_validate(json.loads(outcome.result_body))
    assert event.message_type == OPERATION_PROGRESS
    assert event.payload["message"] == "command accepted for dispatch validation"
