"""Tests for semantic contract validation after checksum verification."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema.exceptions import ValidationError

from ops.contracts.errors import (
    CommonError,
    ErrorCategory,
    OperationTimeoutError,
    ProviderOperationError,
)
from ops.contracts.messages.envelope import MessageEnvelope, assert_supported_major
from ops.contracts.semantic import validate_contract_semantics
from ops.contracts.validate import ValidationResult, validate_contract_tree

ROOT = Path("src/ops/contracts")
ENVELOPE_SCHEMA = ROOT / "jsonschema" / "message_envelope.schema.json"
ERROR_SCHEMA = ROOT / "jsonschema" / "common_error.schema.json"
COMMAND_FIXTURE = ROOT / "fixtures" / "commands" / "connection_validate.json"
ERROR_FIXTURE = ROOT / "fixtures" / "errors" / "provider_authentication_failed.json"


def test_semantics_passes_on_pinned_tree() -> None:
    fixture_count, error = validate_contract_semantics(ROOT)
    assert error is None
    assert fixture_count == 7


def test_malformed_fixture_json_fails_without_leaking_content(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures" / "commands"
    fixtures.mkdir(parents=True)
    secret = "not-json-content-xyzzy"  # pragma: allowlist secret
    (fixtures / "broken.json").write_text(f"{{{secret}", encoding="utf-8")
    (tmp_path / "jsonschema").mkdir()
    fixture_count, error = validate_contract_semantics(tmp_path)
    assert error == "invalid JSON: fixtures/commands/broken.json"
    assert secret not in error


def test_envelope_fixture_validates_with_pydantic_and_jsonschema() -> None:
    raw = json.loads(COMMAND_FIXTURE.read_text(encoding="utf-8"))
    MessageEnvelope.model_validate(raw)
    schema = json.loads(ENVELOPE_SCHEMA.read_text(encoding="utf-8"))
    from jsonschema import Draft202012Validator, FormatChecker

    Draft202012Validator(schema, format_checker=FormatChecker()).validate(raw)


def test_error_fixture_validates_with_pydantic_and_jsonschema() -> None:
    raw = json.loads(ERROR_FIXTURE.read_text(encoding="utf-8"))
    CommonError.model_validate(raw)
    schema = json.loads(ERROR_SCHEMA.read_text(encoding="utf-8"))
    from jsonschema import Draft202012Validator, FormatChecker

    Draft202012Validator(schema, format_checker=FormatChecker()).validate(raw)


def test_jsonschema_rejects_invalid_uuid() -> None:
    raw = json.loads(COMMAND_FIXTURE.read_text(encoding="utf-8"))
    raw["message_id"] = "not-a-uuid"
    schema = json.loads(ENVELOPE_SCHEMA.read_text(encoding="utf-8"))
    from jsonschema import Draft202012Validator, FormatChecker

    with pytest.raises(ValidationError):
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(raw)


def test_jsonschema_rejects_invalid_datetime() -> None:
    raw = json.loads(COMMAND_FIXTURE.read_text(encoding="utf-8"))
    raw["occurred_at"] = "not-a-datetime"
    schema = json.loads(ENVELOPE_SCHEMA.read_text(encoding="utf-8"))
    from jsonschema import Draft202012Validator, FormatChecker

    with pytest.raises(ValidationError):
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(raw)


def test_naive_occurred_at_rejected() -> None:
    raw = json.loads(COMMAND_FIXTURE.read_text(encoding="utf-8"))
    raw["occurred_at"] = "2026-07-17T00:00:00"
    with pytest.raises(ValueError, match="UTC"):
        MessageEnvelope.model_validate(raw)


def test_non_utc_offset_occurred_at_rejected() -> None:
    raw = json.loads(COMMAND_FIXTURE.read_text(encoding="utf-8"))
    raw["occurred_at"] = "2026-07-17T00:00:00+07:00"
    with pytest.raises(ValueError, match="UTC"):
        MessageEnvelope.model_validate(raw)


def test_common_error_schema_rejects_invalid_datetime() -> None:
    raw = json.loads(ERROR_FIXTURE.read_text(encoding="utf-8"))
    raw["occurred_at"] = "not-a-datetime"
    schema = json.loads(ERROR_SCHEMA.read_text(encoding="utf-8"))
    from jsonschema import Draft202012Validator, FormatChecker

    with pytest.raises(ValidationError):
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(raw)


def test_unknown_major_rejected_and_unknown_minor_field_accepted() -> None:
    with pytest.raises(ValueError, match="unsupported major"):
        assert_supported_major("2.0")
    raw = json.loads(COMMAND_FIXTURE.read_text(encoding="utf-8"))
    raw["future_minor_field"] = {"safe": True}
    MessageEnvelope.model_validate(raw)


def test_events_omit_credential_reference() -> None:
    for path in (ROOT / "fixtures" / "events").glob("*.json"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert "credential_reference" not in raw


def test_command_contains_credential_reference() -> None:
    raw = json.loads(COMMAND_FIXTURE.read_text(encoding="utf-8"))
    assert raw["credential_reference"] == "66666666-6666-4666-8666-666666666666"


def test_fixtures_have_no_inline_secrets() -> None:
    for path in (ROOT / "fixtures").rglob("*.json"):
        text = path.read_text(encoding="utf-8").lower()
        for forbidden in ("password", "token", "authorization", "user_data", "private_key"):
            assert forbidden not in text


def test_validate_contract_tree_includes_semantics(tmp_path: Path) -> None:
    import hashlib

    fixtures = tmp_path / "fixtures" / "commands"
    fixtures.mkdir(parents=True)
    bad_bytes = b"{"
    (fixtures / "bad.json").write_bytes(bad_bytes)
    (tmp_path / "jsonschema").mkdir()
    digest = hashlib.sha256(bad_bytes).hexdigest()
    (tmp_path / "checksums.json").write_text(
        json.dumps({"files": {"fixtures/commands/bad.json": digest}}) + "\n",
        encoding="utf-8",
    )
    result = validate_contract_tree(tmp_path)
    assert result == ValidationResult(False, 1, "invalid JSON: fixtures/commands/bad.json")


def test_invalid_json_schema_is_rejected(tmp_path: Path) -> None:
    schemas = tmp_path / "jsonschema"
    schemas.mkdir()
    (schemas / "message_envelope.schema.json").write_text(json.dumps({"type": 7}), encoding="utf-8")
    fixture_count, error = validate_contract_semantics(tmp_path)
    assert fixture_count == 0
    assert error == "invalid JSON Schema: jsonschema/message_envelope.schema.json"


def test_envelope_fixture_requires_envelope_schema(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures" / "commands"
    fixtures.mkdir(parents=True)
    fixtures.joinpath("command.json").write_text("{}", encoding="utf-8")
    fixture_count, error = validate_contract_semantics(tmp_path)
    assert fixture_count == 1
    assert error == "missing file: jsonschema/message_envelope.schema.json"


def test_common_error_behavior_matches_pinned_schema() -> None:
    schema = json.loads(ERROR_SCHEMA.read_text(encoding="utf-8"))
    assert set(ErrorCategory) == set(schema["$defs"]["ErrorCategory"]["enum"])
    assert set(schema["required"]) == {"code", "message", "category", "retryable"}

    timeout = OperationTimeoutError()
    assert timeout.retryable is True
    assert timeout.category is ErrorCategory.TIMEOUT

    secret = "password=synthetic"  # pragma: allowlist secret
    provider = ProviderOperationError(cause=secret)
    assert str(provider) == "Provider operation failed"
    assert secret not in str(provider)
