"""Production OpenStack connection validation handler."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from ops.application.credential_resolver import CpsResolutionError, CredentialResolver
from ops.application.handlers.registry import TypedHandlerFn
from ops.config import Settings
from ops.contracts.errors import CommonError, ErrorCategory
from ops.contracts.messages.delivery import DeliveryMetadata
from ops.contracts.messages.envelope import MessageEnvelope
from ops.contracts.messages.types import OPERATION_COMPLETED, OPERATION_FAILED, OPERATION_PROGRESS
from ops.contracts.validation import CapabilityDocument
from ops.messaging.consumer import HandlerFailedResult, HandlerRetryableError, HandlerSuccess
from ops.observability.redaction import redact_mapping
from ops.openstack.discovery import DiscoveryValidationError, discover_capabilities
from ops.openstack.errors import normalize_openstack_exception
from ops.openstack.factory import openstack_connection


def _event(
    command: MessageEnvelope, message_type: str, payload: dict[str, Any], label: str
) -> bytes:
    message = MessageEnvelope.model_validate(
        {
            "message_id": uuid.uuid5(command.operation_id, label),
            "message_type": message_type,
            "schema_version": command.schema_version,
            "occurred_at": command.occurred_at,
            "correlation_id": command.correlation_id,
            "causation_id": command.message_id,
            "operation_id": command.operation_id,
            "provider_id": command.provider_id,
            "provider_connection_id": command.provider_connection_id,
            "trace_context": redact_mapping(dict(command.trace_context)),
            "payload": payload,
        }
    )
    return json.dumps(
        message.model_dump(mode="json", exclude_none=True), separators=(",", ":")
    ).encode()


def _progress(command: MessageEnvelope, state: str, progress: int, label: str) -> bytes:
    return _event(
        command,
        OPERATION_PROGRESS,
        {"progress": progress, "state": state, "message": "provider validation in progress"},
        label,
    )


def _failure(command: MessageEnvelope, error: CommonError) -> bytes:
    return _event(
        command, OPERATION_FAILED, {"error": error.model_dump(mode="json")}, "validation.failed"
    )


async def connection_validate(
    command: MessageEnvelope,
    _metadata: DeliveryMetadata,
    _routing_key: str,
    *,
    settings: Settings,
) -> HandlerSuccess | HandlerFailedResult | HandlerRetryableError:
    if (
        command.payload != {"validation_mode": "SAFE_READ_ONLY"}
        or command.credential_reference is None
    ):
        return HandlerFailedResult(
            result_routing_key=OPERATION_FAILED,
            result_body=_failure(
                command,
                CommonError(
                    code="INVALID_REQUEST",
                    message="Validation command is invalid",
                    category=ErrorCategory.VALIDATION,
                    retryable=False,
                ),
            ),
        )
    try:
        resolution = await CredentialResolver(settings).resolve(
            command.credential_reference, command.provider_connection_id
        )
    except CpsResolutionError as exc:
        if exc.retryable:
            return HandlerRetryableError(
                error=CommonError(
                    code=exc.code,
                    message="CPS credential resolver unavailable",
                    category=ErrorCategory.NETWORK,
                    retryable=True,
                ),
                retry_reason="CPS_UNAVAILABLE",
            )
        return HandlerFailedResult(
            result_routing_key=OPERATION_FAILED,
            result_body=_failure(
                command,
                CommonError(
                    code=exc.code,
                    message="Credential reference is invalid",
                    category=ErrorCategory.NOT_FOUND,
                    retryable=False,
                ),
            ),
        )
    try:
        progress_running = _progress(command, "RUNNING", 10, "validation.started")
        with openstack_connection(resolution, settings) as conn:
            capabilities: CapabilityDocument = await asyncio.to_thread(discover_capabilities, conn)
        progress_waiting = _progress(command, "WAITING_PROVIDER", 80, "validation.discovery")
        completed = _event(
            command,
            OPERATION_COMPLETED,
            {"result": {"status": "VALID", "capabilities": capabilities.model_dump(mode="json")}},
            "validation.completed",
        )
        return HandlerSuccess(
            result_messages=(
                (OPERATION_PROGRESS, progress_running),
                (OPERATION_PROGRESS, progress_waiting),
                (OPERATION_COMPLETED, completed),
            )
        )
    except DiscoveryValidationError:
        error = CommonError(
            code="PROVIDER_UNAVAILABLE",
            message="Required OpenStack service unavailable",
            category=ErrorCategory.PROVIDER,
            retryable=False,
        )
        return HandlerFailedResult(
            result_routing_key=OPERATION_FAILED, result_body=_failure(command, error)
        )
    except Exception as exc:
        error = normalize_openstack_exception(exc)
        failed = _failure(command, error)
        if error.retryable:
            return HandlerRetryableError(error=error, retry_reason="PROVIDER_UNAVAILABLE")
        return HandlerFailedResult(result_routing_key=OPERATION_FAILED, result_body=failed)


def make_connection_validate(settings: Settings) -> TypedHandlerFn:
    async def handler(
        command: MessageEnvelope, metadata: DeliveryMetadata, routing_key: str
    ) -> HandlerSuccess | HandlerFailedResult | HandlerRetryableError:
        return await connection_validate(command, metadata, routing_key, settings=settings)

    return handler
