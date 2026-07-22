"""Stub handler for openstack.connection.validate without provider calls."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from ops.application.handlers.registry import TypedHandlerFn
from ops.contracts.errors import CommonError
from ops.contracts.messages.delivery import DeliveryMetadata
from ops.contracts.messages.envelope import MessageEnvelope
from ops.contracts.messages.types import (
    OPERATION_COMPLETED,
    OPERATION_FAILED,
    OPERATION_PROGRESS,
)
from ops.identifiers import new_uuid7
from ops.messaging.consumer import HandlerSuccess
from ops.observability.redaction import redact_mapping

PROGRESS_MESSAGE = "command accepted for dispatch validation"

# Canonical synthetic payload shapes from pinned OPS fixtures (not provider-derived).
_DEFAULT_COMPLETED_RESULT: dict[str, Any] = {
    "status": "VALID",
    "capabilities": {
        "compute": True,
        "network": True,
        "image": True,
        "volume": True,
    },
}

_DEFAULT_FAILED_ERROR: dict[str, Any] = {
    "code": "PROVIDER_AUTHENTICATION_FAILED",
    "message": "OpenStack authentication failed",
    "category": "AUTHENTICATION",
    "retryable": False,
    "provider": "OPENSTACK",
    "provider_service": "identity",
    "provider_request_id": "req-synthetic",
    "details": {},
}


def _build_operation_event(
    command: MessageEnvelope,
    *,
    message_type: str,
    payload: dict[str, Any],
    now: datetime | None = None,
    new_message_id: UUID | None = None,
) -> MessageEnvelope:
    occurred_at = now or datetime.now(tz=UTC)
    return MessageEnvelope.model_validate(
        {
            "message_id": str(new_message_id or new_uuid7()),
            "message_type": message_type,
            "schema_version": command.schema_version,
            "occurred_at": occurred_at.isoformat().replace("+00:00", "Z"),
            "correlation_id": str(command.correlation_id),
            "causation_id": str(command.message_id),
            "operation_id": str(command.operation_id),
            "idempotency_key": None,
            "provider_id": str(command.provider_id),
            "provider_connection_id": str(command.provider_connection_id),
            "trace_context": redact_mapping(copy.deepcopy(command.trace_context)),
            "payload": payload,
        }
    )


def build_progress_event(
    command: MessageEnvelope,
    *,
    now: datetime | None = None,
    new_message_id: UUID | None = None,
) -> MessageEnvelope:
    return _build_operation_event(
        command,
        message_type=OPERATION_PROGRESS,
        payload={
            "progress": 0,
            "message": PROGRESS_MESSAGE,
        },
        now=now,
        new_message_id=new_message_id,
    )


def build_completed_event(
    command: MessageEnvelope,
    *,
    result: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    new_message_id: UUID | None = None,
) -> MessageEnvelope:
    result_payload = (
        copy.deepcopy(dict(result))
        if result is not None
        else copy.deepcopy(_DEFAULT_COMPLETED_RESULT)
    )
    return _build_operation_event(
        command,
        message_type=OPERATION_COMPLETED,
        payload={"result": result_payload},
        now=now,
        new_message_id=new_message_id,
    )


def build_failed_event(
    command: MessageEnvelope,
    *,
    error: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    new_message_id: UUID | None = None,
) -> MessageEnvelope:
    occurred_at = now or datetime.now(tz=UTC)
    event_occurred_at_iso = occurred_at.isoformat().replace("+00:00", "Z")
    if error is not None:
        error_data = copy.deepcopy(dict(error))
        error_data.setdefault("occurred_at", event_occurred_at_iso)
    else:
        error_data = copy.deepcopy(_DEFAULT_FAILED_ERROR)
        error_data["occurred_at"] = event_occurred_at_iso
    validated_error = CommonError.model_validate(error_data)
    error_payload = validated_error.model_dump(mode="json", exclude_none=True)
    return _build_operation_event(
        command,
        message_type=OPERATION_FAILED,
        payload={"error": error_payload},
        now=occurred_at,
        new_message_id=new_message_id,
    )


def _serialize_event(event: MessageEnvelope) -> bytes:
    return json.dumps(
        event.model_dump(mode="json", exclude_none=True),
        separators=(",", ":"),
    ).encode()


async def stub_connection_validate(
    command: MessageEnvelope,
    _metadata: DeliveryMetadata,
    _routing_key: str,
) -> HandlerSuccess:
    event = build_progress_event(command)
    return HandlerSuccess(
        result_routing_key=OPERATION_PROGRESS,
        result_body=_serialize_event(event),
    )


def make_stub_connection_validate(
    on_call: Callable[[], None] | None = None,
) -> TypedHandlerFn:
    async def handler(
        command: MessageEnvelope,
        metadata: DeliveryMetadata,
        routing_key: str,
    ) -> HandlerSuccess:
        if on_call is not None:
            on_call()
        return await stub_connection_validate(command, metadata, routing_key)

    return handler
