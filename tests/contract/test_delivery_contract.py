"""OPS Task 6: pinned retry delivery metadata contract tests."""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import ValidationError as PydanticValidationError

ROOT = Path("src/ops/contracts")
FIXTURE_PATH = ROOT / "fixtures" / "transport" / "retry_delivery.json"
SCHEMA_PATH = ROOT / "jsonschema" / "delivery_metadata.schema.json"
PINNED_MANIFEST = ROOT / "cps_checksums.pinned.json"
LOCAL_MANIFEST = ROOT / "checksums.json"


def _schema_validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _valid_wire_headers() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _fresh_wire_headers() -> dict[str, Any]:
    return {
        "x-transport-version": "1.0",
        "x-message-id": "11111111-1111-4111-8111-111111111111",
        "x-correlation-id": "22222222-2222-4222-8222-222222222222",
        "x-attempt": 1,
        "x-max-attempts": 3,
    }


def _broker_dead_letter_headers() -> dict[str, Any]:
    return {
        "x-death": [
            {
                "queue": "ops.command.retry.1.v1",
                "reason": "expired",
                "exchange": "cmp.cloud.retry.v1",
                "routing-keys": ["ops.command.retry.1"],
                "count": 1,
            }
        ],
        "x-first-death-queue": "ops.command.retry.1.v1",
        "x-first-death-reason": "expired",
        "x-first-death-exchange": "cmp.cloud.retry.v1",
        "x-last-death-queue": "ops.command.retry.1.v1",
        "x-last-death-reason": "expired",
        "x-last-death-exchange": "cmp.cloud.retry.v1",
        "x-delivery-count": 1,
    }


def _full_amqp_headers_with_broker_metadata() -> dict[str, Any]:
    headers = _valid_wire_headers()
    headers.update(_broker_dead_letter_headers())
    return headers


def test_delivery_metadata_is_standalone_without_cps_import() -> None:
    module = importlib.import_module("ops.contracts.messages.delivery")
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "cps." not in source
    assert "import cps" not in source


def test_retry_delivery_fixture_validates_with_pydantic_and_jsonschema() -> None:
    from ops.contracts.messages.delivery import DeliveryMetadata

    raw = _valid_wire_headers()
    DeliveryMetadata.model_validate(raw)
    _schema_validator().validate(raw)


def test_model_validate_parses_rabbitmq_headers_directly() -> None:
    from ops.contracts.messages.delivery import (
        HEADER_ATTEMPT,
        HEADER_CORRELATION_ID,
        HEADER_MAX_ATTEMPTS,
        HEADER_MESSAGE_ID,
        HEADER_ORIGINAL_ROUTING_KEY,
        HEADER_RETRY_REASON,
        HEADER_TRANSPORT_VERSION,
        DeliveryMetadata,
    )

    rabbitmq_headers = _valid_wire_headers()
    metadata = DeliveryMetadata.model_validate(rabbitmq_headers)

    assert metadata.attempt == rabbitmq_headers[HEADER_ATTEMPT]
    assert metadata.max_attempts == rabbitmq_headers[HEADER_MAX_ATTEMPTS]
    assert str(metadata.message_id) == rabbitmq_headers[HEADER_MESSAGE_ID]
    assert str(metadata.correlation_id) == rabbitmq_headers[HEADER_CORRELATION_ID]
    assert metadata.transport_version == rabbitmq_headers[HEADER_TRANSPORT_VERSION]
    assert metadata.retry_reason == rabbitmq_headers[HEADER_RETRY_REASON]
    assert metadata.original_routing_key == rabbitmq_headers[HEADER_ORIGINAL_ROUTING_KEY]


def test_model_dump_by_alias_returns_exact_wire_names() -> None:
    from ops.contracts.messages.delivery import (
        HEADER_ATTEMPT,
        HEADER_CORRELATION_ID,
        HEADER_MAX_ATTEMPTS,
        HEADER_MESSAGE_ID,
        HEADER_ORIGINAL_ROUTING_KEY,
        HEADER_RETRY_REASON,
        HEADER_TRANSPORT_VERSION,
        DeliveryMetadata,
    )

    wire = DeliveryMetadata.model_validate(_valid_wire_headers()).model_dump(by_alias=True)

    assert set(wire) == {
        HEADER_TRANSPORT_VERSION,
        HEADER_MESSAGE_ID,
        HEADER_CORRELATION_ID,
        HEADER_ATTEMPT,
        HEADER_MAX_ATTEMPTS,
        HEADER_RETRY_REASON,
        HEADER_ORIGINAL_ROUTING_KEY,
    }
    assert all(key.startswith("x-") for key in wire)


def test_populate_by_name_false_rejects_internal_field_names() -> None:
    from ops.contracts.messages.delivery import DeliveryMetadata

    internal_shape = {
        "transport_version": "1.0",
        "message_id": "11111111-1111-4111-8111-111111111111",
        "correlation_id": "22222222-2222-4222-8222-222222222222",
        "attempt": 2,
        "max_attempts": 3,
        "retry_reason": "TRANSIENT_PROVIDER_ERROR",
        "original_routing_key": "openstack.connection.validate",
    }

    with pytest.raises(PydanticValidationError):
        DeliveryMetadata.model_validate(internal_shape)


def test_fresh_delivery_allows_absent_retry_only_headers() -> None:
    from ops.contracts.messages.delivery import DeliveryMetadata

    raw = _fresh_wire_headers()
    DeliveryMetadata.model_validate(raw)
    _schema_validator().validate(raw)


def test_strict_model_validate_rejects_full_amqp_headers_with_broker_metadata() -> None:
    from ops.contracts.messages.delivery import DeliveryMetadata

    full_headers = _full_amqp_headers_with_broker_metadata()

    with pytest.raises(PydanticValidationError, match="extra_forbidden"):
        DeliveryMetadata.model_validate(full_headers)


def test_parse_delivery_metadata_accepts_full_amqp_headers_with_broker_metadata() -> None:
    from ops.contracts.messages.delivery import (
        DELIVERY_HEADER_NAMES,
        HEADER_ATTEMPT,
        HEADER_CORRELATION_ID,
        HEADER_MAX_ATTEMPTS,
        HEADER_MESSAGE_ID,
        HEADER_ORIGINAL_ROUTING_KEY,
        HEADER_RETRY_REASON,
        HEADER_TRANSPORT_VERSION,
        parse_delivery_metadata,
    )

    full_headers = _full_amqp_headers_with_broker_metadata()
    canonical = _valid_wire_headers()
    metadata = parse_delivery_metadata(full_headers)

    assert metadata.attempt == canonical[HEADER_ATTEMPT]
    assert metadata.max_attempts == canonical[HEADER_MAX_ATTEMPTS]
    assert str(metadata.message_id) == canonical[HEADER_MESSAGE_ID]
    assert str(metadata.correlation_id) == canonical[HEADER_CORRELATION_ID]
    assert metadata.transport_version == canonical[HEADER_TRANSPORT_VERSION]
    assert metadata.retry_reason == canonical[HEADER_RETRY_REASON]
    assert metadata.original_routing_key == canonical[HEADER_ORIGINAL_ROUTING_KEY]

    wire = metadata.model_dump(by_alias=True)
    assert set(wire) == DELIVERY_HEADER_NAMES
    assert "x-death" not in wire
    assert "x-first-death-queue" not in wire
    assert "x-last-death-reason" not in wire
    assert "x-delivery-count" not in wire


def test_parse_delivery_metadata_does_not_mutate_input() -> None:
    from ops.contracts.messages.delivery import parse_delivery_metadata

    full_headers = _full_amqp_headers_with_broker_metadata()
    snapshot = json.loads(json.dumps(full_headers, default=str))

    parse_delivery_metadata(full_headers)

    assert full_headers == snapshot


def test_parse_delivery_metadata_rejects_missing_owned_header_despite_broker_headers() -> None:
    from ops.contracts.messages.delivery import parse_delivery_metadata

    full_headers = _full_amqp_headers_with_broker_metadata()
    full_headers.pop("x-message-id")

    with pytest.raises(PydanticValidationError):
        parse_delivery_metadata(full_headers)


def test_parse_delivery_metadata_rejects_invalid_owned_header_despite_broker_headers() -> None:
    from ops.contracts.messages.delivery import (
        StrictWireTypeError,
        parse_delivery_metadata,
    )

    full_headers = _full_amqp_headers_with_broker_metadata()
    full_headers["x-attempt"] = "2"

    with pytest.raises((PydanticValidationError, StrictWireTypeError)):
        parse_delivery_metadata(full_headers)


def test_parse_delivery_metadata_ignores_unknown_application_headers() -> None:
    from ops.contracts.messages.delivery import DELIVERY_HEADER_NAMES, parse_delivery_metadata

    full_headers = _full_amqp_headers_with_broker_metadata()
    full_headers["x-custom-application-header"] = "unexpected"

    metadata = parse_delivery_metadata(full_headers)
    wire = metadata.model_dump(by_alias=True)

    assert "x-custom-application-header" not in wire
    assert set(wire) == DELIVERY_HEADER_NAMES


def test_parse_delivery_metadata_error_does_not_leak_broker_header_content() -> None:
    from ops.contracts.messages.delivery import parse_delivery_metadata

    full_headers = _full_amqp_headers_with_broker_metadata()
    full_headers["x-attempt"] = True

    with pytest.raises(Exception) as exc_info:
        parse_delivery_metadata(full_headers)

    error_text = str(exc_info.value)
    assert "ops.command.retry.1.v1" not in error_text
    assert "cmp.cloud.retry.v1" not in error_text
    assert "x-death" not in error_text


def test_parse_delivery_metadata_uses_attempt_not_delivery_count() -> None:
    from ops.contracts.messages.delivery import parse_delivery_metadata

    full_headers = _full_amqp_headers_with_broker_metadata()
    full_headers["x-attempt"] = 2
    full_headers["x-delivery-count"] = 99

    metadata = parse_delivery_metadata(full_headers)
    assert metadata.attempt == 2

    full_headers.pop("x-attempt")
    with pytest.raises(PydanticValidationError):
        parse_delivery_metadata(full_headers)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda raw: raw.update({"x-attempt": "2"}),
        lambda raw: raw.update({"x-attempt": True}),
        lambda raw: raw.update({"x-attempt": 2.0}),
        lambda raw: raw.update({"x-max-attempts": "3"}),
        lambda raw: raw.update({"x-max-attempts": True}),
        lambda raw: raw.update({"x-extra-header": "unexpected"}),
        lambda raw: raw.pop("x-message-id"),
        lambda raw: raw.update({"x-message-id": "not-a-uuid"}),
        lambda raw: raw.update({"x-transport-version": "2.0"}),
        lambda raw: raw.update({"x-original-routing-key": "openstack.unlisted.action"}),
        lambda raw: raw.update({"x-retry-reason": "lowercase_reason"}),
        lambda raw: raw.update({"x-retry-reason": "BAD\nREASON"}),
        lambda raw: raw.update({"x-retry-reason": "X" * 65}),
    ],
    ids=[
        "attempt-string",
        "attempt-bool",
        "attempt-float",
        "max-attempts-string",
        "max-attempts-bool",
        "extra-header",
        "missing-message-id",
        "malformed-uuid",
        "unsupported-version",
        "unallowlisted-routing-key",
        "invalid-retry-reason-case",
        "invalid-retry-reason-newline",
        "invalid-retry-reason-length",
    ],
)
def test_invalid_wire_metadata_rejected_by_pydantic_and_jsonschema(
    mutator: Callable[[dict[str, Any]], None],
) -> None:
    from ops.contracts.messages.delivery import (
        DeliveryMetadata,
        StrictWireTypeError,
        assert_strict_wire_header_types,
    )

    raw = _valid_wire_headers()
    mutator(raw)

    with pytest.raises(PydanticValidationError):
        DeliveryMetadata.model_validate(raw)
    with pytest.raises((JsonSchemaValidationError, StrictWireTypeError)):
        assert_strict_wire_header_types(raw)
        _schema_validator().validate(raw)


@pytest.mark.parametrize(
    "routing_key",
    [
        "openstack.retry",
        "ops.command.retry.1",
        "ops.command.retry.2",
        "openstack.#",
        "openstack.arbitrary.action",
    ],
)
def test_forbidden_routing_keys_rejected(routing_key: str) -> None:
    from ops.contracts.messages.delivery import DeliveryMetadata

    raw = _valid_wire_headers()
    raw["x-original-routing-key"] = routing_key

    with pytest.raises(PydanticValidationError):
        DeliveryMetadata.model_validate(raw)
    with pytest.raises(JsonSchemaValidationError):
        _schema_validator().validate(raw)


@pytest.mark.parametrize(
    "missing_field",
    ["x-retry-reason", "x-original-routing-key"],
)
def test_retry_delivery_requires_retry_only_headers(missing_field: str) -> None:
    from ops.contracts.messages.delivery import DeliveryMetadata

    raw = _valid_wire_headers()
    raw.pop(missing_field)

    with pytest.raises(PydanticValidationError):
        DeliveryMetadata.model_validate(raw)
    with pytest.raises(JsonSchemaValidationError):
        _schema_validator().validate(raw)


def test_attempt_greater_than_max_attempts_is_pydantic_only_invariant() -> None:
    from ops.contracts.messages.delivery import DeliveryMetadata

    raw = _valid_wire_headers()
    raw["x-attempt"] = 4
    raw["x-max-attempts"] = 3

    with pytest.raises(PydanticValidationError, match="max_attempts"):
        DeliveryMetadata.model_validate(raw)

    # JSON Schema does not compare sibling numeric fields without extensions.
    _schema_validator().validate(raw)


def test_pinned_manifest_matches_local_manifest_bytes() -> None:
    assert LOCAL_MANIFEST.read_bytes() == PINNED_MANIFEST.read_bytes()


def test_retry_delivery_fixture_checksum_matches_manifest() -> None:
    manifest = json.loads(PINNED_MANIFEST.read_text(encoding="utf-8"))
    label = "fixtures/transport/retry_delivery.json"
    digest = hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
    assert manifest["files"][label] == digest
