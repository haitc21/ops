"""Semantic validation for pinned contract fixtures and schemas."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError
from pydantic import ValidationError as PydanticValidationError

from ops.contracts.errors import CommonError
from ops.contracts.messages.delivery import DeliveryMetadata, assert_strict_wire_header_types
from ops.contracts.messages.envelope import MessageEnvelope
from ops.contracts.messages.inventory import InventoryBatchPayload
from ops.contracts.messages.resource_operations import (
    ResourceOperationRequest,
    ResourceOperationResult,
)
from ops.contracts.validation import validate_validation_event

_FORBIDDEN_SECRET_TOKENS = ("password", "token", "authorization", "user_data", "private_key")
_ENVELOPE_PREFIXES = ("fixtures/commands/", "fixtures/events/")
_ERROR_PREFIX = "fixtures/errors/"
_TRANSPORT_PREFIX = "fixtures/transport/"
_ENVELOPE_SCHEMA = "jsonschema/message_envelope.schema.json"
_ERROR_SCHEMA = "jsonschema/common_error.schema.json"
_DELIVERY_SCHEMA = "jsonschema/delivery_metadata.schema.json"
_RESOURCE_OPERATION_PREFIX = "fixtures/resource_operations/"
_RESOURCE_OPERATION_SCHEMA = "jsonschema/resource_operation.schema.json"


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
    message_type = raw.get("message_type")
    if label.startswith("fixtures/commands/"):
        if message_type == "openstack.connection.validate" and raw.get("payload") != {
            "validation_mode": "SAFE_READ_ONLY"
        }:
            return f"validation command payload is not canonical: {label}"
    message_type = raw.get("message_type")
    if isinstance(message_type, str) and (
        message_type.startswith("cloud.connection.validation.")
        or message_type.startswith("cloud.operation.")
    ):
        try:
            validate_validation_event(raw)
        except (TypeError, ValueError):
            return f"fixture failed validation event semantics: {label}"
    if message_type == "cloud.inventory.batch":
        try:
            InventoryBatchPayload.model_validate(raw.get("payload", {}))
        except (TypeError, ValueError):
            return f"fixture failed inventory batch semantics: {label}"
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


def _validate_delivery_fixture(
    label: str,
    raw: dict[str, object],
    validator: Draft202012Validator,
) -> str | None:
    try:
        assert_strict_wire_header_types(raw)
    except ValueError:
        return f"fixture failed strict wire type validation: {label}"
    try:
        DeliveryMetadata.model_validate(raw)
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
    has_delivery_schema = (base / _DELIVERY_SCHEMA).is_file()
    has_envelope_fixtures = any(
        label.startswith(_ENVELOPE_PREFIXES) for label, _raw in parsed_fixtures
    )
    has_error_fixtures = any(label.startswith(_ERROR_PREFIX) for label, _raw in parsed_fixtures)
    has_transport_fixtures = any(
        label.startswith(_TRANSPORT_PREFIX) for label, _raw in parsed_fixtures
    )
    has_resource_operation_fixtures = any(
        label.startswith(_RESOURCE_OPERATION_PREFIX) for label, _raw in parsed_fixtures
    )
    if has_envelope_fixtures and not has_envelope_schema:
        return len(fixture_paths), f"missing file: {_ENVELOPE_SCHEMA}"
    if has_error_fixtures and not has_error_schema:
        return len(fixture_paths), f"missing file: {_ERROR_SCHEMA}"
    if has_transport_fixtures and not has_delivery_schema:
        return len(fixture_paths), f"missing file: {_DELIVERY_SCHEMA}"
    if has_resource_operation_fixtures and not (base / _RESOURCE_OPERATION_SCHEMA).is_file():
        return len(fixture_paths), f"missing file: {_RESOURCE_OPERATION_SCHEMA}"

    envelope_validator: Draft202012Validator | None = None
    error_validator: Draft202012Validator | None = None
    delivery_validator: Draft202012Validator | None = None
    resource_operation_validator: Draft202012Validator | None = None
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
    if has_delivery_schema:
        loaded = _schema_validator(base / _DELIVERY_SCHEMA, label=_DELIVERY_SCHEMA)
        if isinstance(loaded, str):
            return len(fixture_paths), loaded
        delivery_validator = loaded
    if has_resource_operation_fixtures:
        loaded = _schema_validator(
            base / _RESOURCE_OPERATION_SCHEMA, label=_RESOURCE_OPERATION_SCHEMA
        )
        if isinstance(loaded, str):
            return len(fixture_paths), loaded
        resource_operation_validator = loaded

    for label, raw in parsed_fixtures:
        secret_error = _scan_fixture_secrets(base / label, label=label)
        if secret_error is not None:
            return len(fixture_paths), secret_error

        if label.startswith(_ERROR_PREFIX):
            if error_validator is None:
                return len(fixture_paths), f"missing file: {_ERROR_SCHEMA}"
            semantic_error = _validate_error_fixture(label, raw, error_validator)
        elif label.startswith(_TRANSPORT_PREFIX):
            if delivery_validator is None:
                return len(fixture_paths), f"missing file: {_DELIVERY_SCHEMA}"
            semantic_error = _validate_delivery_fixture(label, raw, delivery_validator)
        elif label.startswith(_RESOURCE_OPERATION_PREFIX):
            if resource_operation_validator is None:
                return len(fixture_paths), f"missing file: {_RESOURCE_OPERATION_SCHEMA}"
            try:
                if label.endswith("/request.json"):
                    ResourceOperationRequest.model_validate(raw)
                elif label.endswith("/result.json"):
                    ResourceOperationResult.model_validate(raw)
                else:
                    return len(fixture_paths), f"unsupported fixture path: {label}"
                resource_operation_validator.validate(raw)
            except (PydanticValidationError, ValidationError):
                semantic_error = f"fixture failed resource operation validation: {label}"
            else:
                semantic_error = None
        elif any(label.startswith(prefix) for prefix in _ENVELOPE_PREFIXES):
            if envelope_validator is None:
                return len(fixture_paths), f"missing file: {_ENVELOPE_SCHEMA}"
            semantic_error = _validate_envelope_fixture(label, raw, envelope_validator)
        elif has_envelope_schema or has_error_schema or has_delivery_schema:
            semantic_error = f"unsupported fixture path: {label}"
        else:
            semantic_error = None

        if semantic_error is not None:
            return len(fixture_paths), semantic_error

    return len(fixture_paths), None
