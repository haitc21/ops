"""Replay-safe Nova server-image snapshots with no image bytes on the wire."""

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
from ops.contracts.messages.instance_snapshot_operations import InstanceSnapshotRequest
from ops.contracts.messages.types import OPERATION_COMPLETED, OPERATION_FAILED, OPERATION_PROGRESS
from ops.messaging.consumer import HandlerFailedResult, HandlerRetryableError, HandlerSuccess
from ops.observability.redaction import redact_mapping
from ops.openstack.errors import normalize_openstack_exception
from ops.openstack.factory import openstack_connection
from ops.openstack.inventory import map_resource
from ops.openstack.waiter import WaiterConfig, WaiterTimeoutError, wait_for_state


def _event(
    command: MessageEnvelope,
    payload: dict[str, Any],
    label: str,
    message_type: str = OPERATION_COMPLETED,
) -> bytes:
    event = MessageEnvelope.model_validate(
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
            "payload": payload if message_type == OPERATION_PROGRESS else {"result": payload},
        }
    )
    return json.dumps(
        event.model_dump(mode="json", exclude_none=True), separators=(",", ":")
    ).encode()


def _failure(command: MessageEnvelope, error: CommonError) -> bytes:
    return _event(
        command,
        {"error": error.model_dump(mode="json")},
        "instance.snapshot.failed",
        OPERATION_FAILED,
    )


def _non_retryable_error(code: str, *, details: dict[str, object] | None = None) -> CommonError:
    return CommonError(
        code=code,
        message="OpenStack provider request failed",
        category=ErrorCategory.CONFLICT
        if code == "INVALID_RESOURCE_STATE"
        else ErrorCategory.CAPABILITY,
        retryable=False,
        provider="OPENSTACK",
        provider_service="compute",
        details=details or {},
    )


def _image_has_marker(image: Any, operation_id: uuid.UUID) -> bool:
    marker = str(operation_id)
    properties = getattr(image, "properties", None) or {}
    return (
        isinstance(properties, dict) and properties.get("cmp_operation_id") == marker
    ) or getattr(image, "cmp_operation_id", None) == marker


async def _find_snapshot_by_operation(connection: Any, operation_id: uuid.UUID) -> Any | None:
    images = await asyncio.to_thread(lambda: list(connection.image.images()))
    return next((image for image in images if _image_has_marker(image, operation_id)), None)


async def instance_snapshot(
    command: MessageEnvelope,
    _metadata: DeliveryMetadata | None,
    _routing_key: str,
    *,
    settings: Settings,
) -> HandlerSuccess | HandlerFailedResult | HandlerRetryableError:
    try:
        request = InstanceSnapshotRequest.model_validate(command.payload)
    except ValueError:
        return HandlerFailedResult()
    if (
        request.operation_id != command.operation_id
        or request.provider_connection_id != command.provider_connection_id
    ):
        return HandlerFailedResult()
    try:
        resolution = await CredentialResolver(settings).resolve(command.provider_connection_id)
        with openstack_connection(resolution, settings) as connection:
            if hasattr(connection, "has_service") and not connection.has_service("image"):
                error = _non_retryable_error(
                    "CAPABILITY_NOT_SUPPORTED", details={"feature": "instance.snapshot"}
                )
                return HandlerFailedResult(
                    result_routing_key=OPERATION_FAILED, result_body=_failure(command, error)
                )
            compute = connection.compute
            server = await asyncio.to_thread(
                compute.get_server, request.instance_provider_resource_id
            )
            status = str(getattr(server, "status", "")).upper()
            owner = getattr(server, "project_id", None) or getattr(server, "tenant_id", None)
            if owner and str(owner) != request.project_provider_resource_id:
                error = _non_retryable_error("RESOURCE_OWNERSHIP_MISMATCH")
                return HandlerFailedResult(
                    result_routing_key=OPERATION_FAILED, result_body=_failure(command, error)
                )
            if status != "ACTIVE":
                error = _non_retryable_error(
                    "INVALID_RESOURCE_STATE", details={"provider_status": status}
                )
                return HandlerFailedResult(
                    result_routing_key=OPERATION_FAILED, result_body=_failure(command, error)
                )
            image = await _find_snapshot_by_operation(connection, command.operation_id)
            if image is None:
                image = await asyncio.to_thread(
                    compute.create_server_image,
                    server,
                    request.name,
                    {**request.metadata, "cmp_operation_id": str(command.operation_id)},
                    False,
                    int(settings.openstack_timeout_seconds),
                )
            progress = _event(
                command,
                {
                    "progress": 25,
                    "state": "RUNNING",
                    "message": "instance snapshot started",
                    "provider_resource_id": str(getattr(image, "id", "")),
                },
                "instance.snapshot.progress",
                OPERATION_PROGRESS,
            )
            image_id = str(getattr(image, "id", ""))
            if not image_id:
                raise RuntimeError("snapshot image did not return an identity")
            image = await wait_for_state(
                lambda: asyncio.to_thread(connection.image.get_image, image_id),
                config=WaiterConfig(
                    target_states=frozenset({"ACTIVE"}),
                    timeout_seconds=min(300.0, settings.openstack_timeout_seconds * 10),
                    terminal_error_states=frozenset({"ERROR", "KILLED"}),
                ),
            )
            resource = map_resource("image", image)
            attributes = resource.setdefault("attributes", {})
            attributes.update(
                {
                    "image_type": "snapshot",
                    "source_instance_provider_resource_id": request.instance_provider_resource_id,
                    "owner_project_provider_resource_id": request.project_provider_resource_id,
                }
            )
            result = {
                "action": "SNAPSHOT",
                "resource_type": "image",
                "operation": "snapshot",
                "provider_resource_id": image_id,
                "resource": resource,
            }
            return HandlerSuccess(
                result_messages=(
                    (OPERATION_PROGRESS, progress),
                    (OPERATION_COMPLETED, _event(command, result, "instance.snapshot.completed")),
                )
            )
    except CpsResolutionError as exc:
        return (
            HandlerRetryableError(retry_reason="CPS_UNAVAILABLE")
            if exc.retryable
            else HandlerFailedResult()
        )
    except WaiterTimeoutError:
        return HandlerRetryableError(retry_reason="PROVIDER_TIMEOUT")
    except Exception as exc:
        error = normalize_openstack_exception(exc, service="compute")
        if error.retryable:
            return HandlerRetryableError(error=error, retry_reason="PROVIDER_UNAVAILABLE")
        return HandlerFailedResult(
            result_routing_key=OPERATION_FAILED, result_body=_failure(command, error)
        )


def make_instance_snapshot(settings: Settings) -> TypedHandlerFn:
    async def handler(
        command: MessageEnvelope, metadata: DeliveryMetadata, routing_key: str
    ) -> HandlerSuccess | HandlerFailedResult | HandlerRetryableError:
        return await instance_snapshot(command, metadata, routing_key, settings=settings)

    return handler
