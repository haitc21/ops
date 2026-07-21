"""Unit tests for command envelope validation."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from uuid import UUID

import pytest

from ops.application.validation import EnvelopeReject, validate_command_envelope
from ops.contracts.messages.envelope import MessageEnvelope

COMMAND_FIXTURE = Path("src/ops/contracts/fixtures/commands/connection_validate.json").read_text(
    encoding="utf-8"
)


def _command_dict() -> dict:
    return json.loads(COMMAND_FIXTURE)


def test_valid_command_fixture_passes_validation() -> None:
    envelope = validate_command_envelope(_command_dict())
    assert isinstance(envelope, MessageEnvelope)
    assert envelope.message_type == "openstack.connection.validate"


@pytest.mark.parametrize(
    "field",
    [
        "message_id",
        "message_type",
        "schema_version",
        "occurred_at",
        "correlation_id",
        "operation_id",
        "provider_id",
        "provider_connection_id",
    ],
)
def test_missing_required_field_rejects(field: str) -> None:
    data = _command_dict()
    data.pop(field)
    with pytest.raises(EnvelopeReject) as exc_info:
        validate_command_envelope(data)
    assert exc_info.value.code == "invalid_envelope"


def test_invalid_uuid_rejects() -> None:
    data = _command_dict()
    data["message_id"] = "not-a-uuid"
    with pytest.raises(EnvelopeReject):
        validate_command_envelope(data)


def test_naive_occurred_at_rejects() -> None:
    data = _command_dict()
    data["occurred_at"] = "2026-07-17T00:00:00"
    with pytest.raises(EnvelopeReject):
        validate_command_envelope(data)


def test_non_utc_occurred_at_rejects() -> None:
    data = _command_dict()
    data["occurred_at"] = "2026-07-17T00:00:00+07:00"
    with pytest.raises(EnvelopeReject):
        validate_command_envelope(data)


def test_unsupported_major_rejects() -> None:
    data = _command_dict()
    data["schema_version"] = "2.0"
    with pytest.raises(EnvelopeReject) as exc_info:
        validate_command_envelope(data)
    assert exc_info.value.code == "unsupported_major"


def test_additive_unknown_top_level_field_is_compatible() -> None:
    data = _command_dict()
    data["future_minor_field"] = {"safe": True}
    envelope = validate_command_envelope(data)
    assert envelope.message_type == "openstack.connection.validate"


def test_json_array_rejects() -> None:
    with pytest.raises(EnvelopeReject):
        validate_command_envelope([])  # type: ignore[arg-type]


def test_json_scalar_rejects() -> None:
    with pytest.raises(EnvelopeReject):
        validate_command_envelope("command")  # type: ignore[arg-type]


def test_envelope_reject_does_not_embed_payload() -> None:
    data = _command_dict()
    secret = "password=synthetic"  # pragma: allowlist secret
    data["message_id"] = "not-a-uuid"
    data["payload"] = {"token": secret}
    with pytest.raises(EnvelopeReject) as exc_info:
        validate_command_envelope(data)
    assert secret not in str(exc_info.value)


def test_invalid_utf8_bytes_rejects() -> None:
    with pytest.raises(EnvelopeReject):
        validate_command_envelope(b"\xff\xfe")  # type: ignore[arg-type]


def test_malformed_json_string_rejects() -> None:
    with pytest.raises(EnvelopeReject):
        validate_command_envelope("{not-json")


def test_validate_preserves_ids_without_mutation() -> None:
    data = _command_dict()
    original = copy.deepcopy(data)
    envelope = validate_command_envelope(data)
    assert data == original
    assert envelope.correlation_id == UUID(str(original["correlation_id"]))


def test_invalid_schema_version_format_rejects() -> None:
    data = _command_dict()
    data["schema_version"] = "v1"
    with pytest.raises(EnvelopeReject):
        validate_command_envelope(data)


def test_empty_message_type_rejects() -> None:
    data = _command_dict()
    data["message_type"] = ""
    with pytest.raises(EnvelopeReject):
        validate_command_envelope(data)
