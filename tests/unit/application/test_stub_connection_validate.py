"""Unit tests for stub connection validate handler."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from ops.application.handlers.stub_connection_validate import build_progress_event
from ops.contracts.messages.envelope import MessageEnvelope
from ops.contracts.messages.types import OPERATION_PROGRESS
from ops.identifiers import new_uuid7

COMMAND_FIXTURE = json.loads(
    Path("src/ops/contracts/fixtures/commands/connection_validate.json").read_text(
        encoding="utf-8",
    )
)


def test_new_uuid7_is_version_7() -> None:
    assert new_uuid7().version == 7


def test_new_uuid7_ids_are_monotonic() -> None:
    first = new_uuid7()
    second = new_uuid7()
    assert second > first


def test_build_progress_event_preserves_command_ids() -> None:
    command = MessageEnvelope.model_validate(COMMAND_FIXTURE)
    fixed_now = datetime(2026, 7, 17, 0, 0, 1, tzinfo=UTC)
    event = build_progress_event(command, now=fixed_now, new_message_id=UUID(int=7))
    assert event.correlation_id == command.correlation_id
    assert event.causation_id == command.message_id
    assert event.operation_id == command.operation_id
    assert event.provider_id == command.provider_id
    assert event.provider_connection_id == command.provider_connection_id
    assert event.schema_version == "1.0"
    assert event.message_type == OPERATION_PROGRESS
    assert event.idempotency_key is None


def test_build_progress_event_uses_uuidv7_and_utc() -> None:
    command = MessageEnvelope.model_validate(COMMAND_FIXTURE)
    message_id = new_uuid7()
    fixed_now = datetime(2026, 7, 17, 0, 0, 1, tzinfo=UTC)
    event = build_progress_event(command, now=fixed_now, new_message_id=message_id)
    assert event.message_id == message_id
    assert event.message_id.version == 7
    assert event.occurred_at == fixed_now
    assert event.occurred_at.tzinfo == UTC


def test_build_progress_event_omits_credential_reference() -> None:
    command = MessageEnvelope.model_validate(COMMAND_FIXTURE)
    event = build_progress_event(command)
    dumped = event.model_dump(mode="json", exclude_none=True)
    assert "credential_reference" not in dumped


def test_build_progress_event_copies_trace_context_without_alias() -> None:
    command = MessageEnvelope.model_validate(COMMAND_FIXTURE)
    command.trace_context["traceparent"] = "00-abc-def-01"
    event = build_progress_event(command)
    event.trace_context["traceparent"] = "mutated"
    assert command.trace_context["traceparent"] == "00-abc-def-01"


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


def test_serialized_event_validates_as_message_envelope() -> None:
    command = MessageEnvelope.model_validate(COMMAND_FIXTURE)
    event = build_progress_event(command)
    raw = json.loads(json.dumps(event.model_dump(mode="json")))
    validated = MessageEnvelope.model_validate(raw)
    assert validated.message_type == OPERATION_PROGRESS


def test_serialized_event_excludes_sensitive_command_payload() -> None:
    command = MessageEnvelope.model_validate(COMMAND_FIXTURE)
    event = build_progress_event(command)
    body = json.dumps(event.model_dump(mode="json"))
    lowered = body.lower()
    for forbidden in ("auth_url", "password", "token", "authorization", "user_data"):
        assert forbidden not in lowered
