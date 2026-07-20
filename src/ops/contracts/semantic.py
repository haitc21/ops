"""Semantic validation for pinned contract fixtures and schemas."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError
from pydantic import ValidationError as PydanticValidationError

from ops.contracts.errors import CommonError
from ops.contracts.messages.envelope import MessageEnvelope

_FORBIDDEN_SECRET_TOKENS = ("password", "token", "authorization", "user_data", "private_key")
_ENVELOPE_PREFIXES = ("fixtures/commands/", "fixtures/events/")
_ERROR_PREFIX = "fixtures/errors/"
_ENVELOPE_SCHEMA = "jsonschema/message_envelope.schema.json"
_ERROR_SCHEMA = "jsonschema/common_error.schema.json"


def _load_json_object(path: Path, *, label: str) -> tuple[object | None, str | None]:
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError:
        return None, f"missing file: {label}"
    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError:
        return None, f"invalid JSON: {label}"
    if not isinstance(raw, dict):
        return None, f"JSON root must be an object: {label}"
    return raw, None


def _schema_validator(schema_path: Path, *, label: str) -> Draft202012Validator | str:
    schema, error = _load_json_object(schema_path, label=label)
    if error is not None:
        return error
    assert isinstance(schema, dict)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        return f"invalid JSON Schema: {label}"
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _validate_envelope_fixture(
    label: str,
    raw: dict[str, object],
    validator: Draft202012Validator,
) -> str | None:
    try:
        MessageEnvelope.model_validate(raw)
    except PydanticValidationError:
        return f"fixture failed Pydantic validation: {label}"
    try:
        validator.validate(raw)
    except ValidationError:
        return f"fixture failed JSON Schema validation: {label}"
    if label.startswith("fixtures/commands/"):
        if "credential_reference" not in raw:
            return f"command fixture missing credential_reference: {label}"
    elif "credential_reference" in raw:
        return f"event fixture must omit credential_reference: {label}"
    return None


def _validate_error_fixture(
    label: str,
    raw: dict[str, object],
    validator: Draft202012Validator,
) -> str | None:
    try:
        CommonError.model_validate(raw)
    except PydanticValidationError:
        return f"fixture failed Pydantic validation: {label}"
    try:
        validator.validate(raw)
    except ValidationError:
        return f"fixture failed JSON Schema validation: {label}"
    return None


def _scan_fixture_secrets(path: Path, *, label: str) -> str | None:
    text = path.read_text(encoding="utf-8").lower()
    for token in _FORBIDDEN_SECRET_TOKENS:
        if token in text:
            return f"fixture contains forbidden token: {label}"
    return None


def validate_contract_semantics(base: Path) -> tuple[int, str | None]:
    fixture_paths = sorted(
        path
        for path in base.rglob("*")
        if path.is_file() and path.suffix == ".json" and "fixtures" in path.parts
    )
    schema_dir = base / "jsonschema"
    schema_paths = sorted(path for path in schema_dir.glob("*.json") if path.is_file())

    for schema_path in schema_paths:
        label = schema_path.relative_to(base).as_posix()
        _, error = _load_json_object(schema_path, label=label)
        if error is not None:
            return len(fixture_paths), error

    parsed_fixtures: list[tuple[str, dict[str, object]]] = []
    for fixture_path in fixture_paths:
        label = fixture_path.relative_to(base).as_posix()
        raw, error = _load_json_object(fixture_path, label=label)
        if error is not None:
            return len(fixture_paths), error
        assert isinstance(raw, dict)
        parsed_fixtures.append((label, raw))

    has_envelope_schema = (base / _ENVELOPE_SCHEMA).is_file()
    has_error_schema = (base / _ERROR_SCHEMA).is_file()
    has_envelope_fixtures = any(
        label.startswith(_ENVELOPE_PREFIXES) for label, _raw in parsed_fixtures
    )
    has_error_fixtures = any(label.startswith(_ERROR_PREFIX) for label, _raw in parsed_fixtures)
    if has_envelope_fixtures and not has_envelope_schema:
        return len(fixture_paths), f"missing file: {_ENVELOPE_SCHEMA}"
    if has_error_fixtures and not has_error_schema:
        return len(fixture_paths), f"missing file: {_ERROR_SCHEMA}"

    envelope_validator: Draft202012Validator | None = None
    error_validator: Draft202012Validator | None = None
    if has_envelope_schema:
        loaded = _schema_validator(base / _ENVELOPE_SCHEMA, label=_ENVELOPE_SCHEMA)
        if isinstance(loaded, str):
            return len(fixture_paths), loaded
        envelope_validator = loaded
    if has_error_schema:
        loaded = _schema_validator(base / _ERROR_SCHEMA, label=_ERROR_SCHEMA)
        if isinstance(loaded, str):
            return len(fixture_paths), loaded
        error_validator = loaded

    for label, raw in parsed_fixtures:
        secret_error = _scan_fixture_secrets(base / label, label=label)
        if secret_error is not None:
            return len(fixture_paths), secret_error

        if label.startswith(_ERROR_PREFIX):
            if error_validator is None:
                return len(fixture_paths), f"missing file: {_ERROR_SCHEMA}"
            semantic_error = _validate_error_fixture(label, raw, error_validator)
        elif any(label.startswith(prefix) for prefix in _ENVELOPE_PREFIXES):
            if envelope_validator is None:
                return len(fixture_paths), f"missing file: {_ENVELOPE_SCHEMA}"
            semantic_error = _validate_envelope_fixture(label, raw, envelope_validator)
        elif has_envelope_schema or has_error_schema:
            semantic_error = f"unsupported fixture path: {label}"
        else:
            semantic_error = None

        if semantic_error is not None:
            return len(fixture_paths), semantic_error

    return len(fixture_paths), None
