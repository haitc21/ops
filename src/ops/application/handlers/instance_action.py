"""OpenStack VM detail and lifecycle mutation handlers."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from openstack import exceptions as os_exc

from ops.application.credential_resolver import CpsResolutionError, CredentialResolver
from ops.application.handlers.registry import TypedHandlerFn
from ops.config import Settings
from ops.contracts.errors import CommonError
from ops.contracts.messages.delivery import DeliveryMetadata
from ops.contracts.messages.envelope import MessageEnvelope
from ops.contracts.messages.instance import InstanceAction, InstanceCommandPayload
from ops.contracts.messages.types import OPERATION_COMPLETED, OPERATION_FAILED, OPERATION_PROGRESS
from ops.messaging.consumer import HandlerFailedResult, HandlerRetryableError, HandlerSuccess
from ops.observability.redaction import redact_mapping
from ops.openstack.errors import normalize_openstack_exception
from ops.openstack.factory import openstack_connection
from ops.openstack.inventory import map_resource
from ops.openstack.waiter import WaiterConfig, wait_for_state


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
    event = MessageEnvelope.model_validate(
        {
            "message_id": uuid.uuid5(command.operation_id, "instance.action.failed"),
            "message_type": OPERATION_FAILED,
            "schema_version": command.schema_version,
            "occurred_at": command.occurred_at,
            "correlation_id": command.correlation_id,
            "causation_id": command.message_id,
            "operation_id": command.operation_id,
            "provider_id": command.provider_id,
            "provider_connection_id": command.provider_connection_id,
            "trace_context": redact_mapping(dict(command.trace_context)),
            "payload": {"error": error.model_dump(mode="json")},
        }
    )
    return json.dumps(
        event.model_dump(mode="json", exclude_none=True), separators=(",", ":")
    ).encode()


async def instance_action(
    command: MessageEnvelope,
    _metadata: DeliveryMetadata,
    _routing_key: str,
    *,
    settings: Settings,
    expected_action: InstanceAction,
) -> HandlerSuccess | HandlerFailedResult | HandlerRetryableError:
    try:
        payload = InstanceCommandPayload.model_validate(command.payload)
    except ValueError:
        return HandlerFailedResult()
    if payload.action is not expected_action or command.credential_reference is None:
        return HandlerFailedResult()
    provider_id = payload.instance_provider_resource_id
    if provider_id is None:
        return HandlerFailedResult()
    try:
        resolution = await CredentialResolver(settings).resolve(
            command.credential_reference, command.provider_connection_id
        )
        with openstack_connection(resolution, settings) as connection:
            compute = connection.compute
            try:
                server = await asyncio.to_thread(compute.get_server, provider_id)
            except os_exc.ResourceNotFound:
                if expected_action is not InstanceAction.DELETE:
                    raise
                tombstone_result: dict[str, Any] = {
                    "action": expected_action.value,
                    "instance": {
                        "provider_resource_id": provider_id,
                        "name": provider_id,
                        "lifecycle_state": "DELETED",
                        "attributes": {},
                    },
                    "ports": [],
                    "volumes": [],
                }
                return HandlerSuccess(
                    result_messages=(
                        (
                            OPERATION_COMPLETED,
                            _event(command, tombstone_result, "instance.completed"),
                        ),
                    )
                )
            if expected_action is InstanceAction.START and str(server.status).upper() == "SHUTOFF":
                await asyncio.to_thread(compute.start_server, server)
            elif expected_action is InstanceAction.STOP and str(server.status).upper() == "ACTIVE":
                await asyncio.to_thread(compute.stop_server, server)
            elif expected_action is InstanceAction.REBOOT:
                await asyncio.to_thread(
                    compute.reboot_server, server, reboot_type=payload.reboot_type or "SOFT"
                )
            elif expected_action is InstanceAction.DELETE:
                await asyncio.to_thread(compute.delete_server, server)
            if expected_action is InstanceAction.GET:
                result_instance = map_resource("instance", server)
            elif expected_action is InstanceAction.DELETE:
                result_instance = {
                    "provider_resource_id": provider_id,
                    "name": getattr(server, "name", provider_id),
                    "lifecycle_state": "DELETED",
                    "attributes": {},
                }
            else:
                target = "SHUTOFF" if expected_action is InstanceAction.STOP else "ACTIVE"
                server = await wait_for_state(
                    lambda: asyncio.to_thread(compute.get_server, provider_id),
                    config=WaiterConfig(target_states=frozenset({target})),
                )
                result_instance = map_resource("instance", server)
            operation_result: dict[str, Any] = {
                "action": expected_action.value,
                "instance": result_instance,
                "ports": [],
                "volumes": [],
            }
            progress = _event(
                command,
                {"progress": 20, "state": "RUNNING", "message": "instance action started"},
                "instance.action.progress.running",
                OPERATION_PROGRESS,
            )
            return HandlerSuccess(
                result_messages=(
                    (OPERATION_PROGRESS, progress),
                    (OPERATION_COMPLETED, _event(command, operation_result, "instance.completed")),
                )
            )
    except CpsResolutionError as exc:
        return (
            HandlerRetryableError(retry_reason="CPS_UNAVAILABLE")
            if exc.retryable
            else HandlerFailedResult()
        )
    except Exception as exc:
        error = normalize_openstack_exception(exc, service="compute")
        if error.retryable:
            return HandlerRetryableError(error=error, retry_reason="PROVIDER_UNAVAILABLE")
        return HandlerFailedResult(
            result_routing_key=OPERATION_FAILED, result_body=_failure(command, error)
        )


def make_instance_action(settings: Settings, action: InstanceAction) -> TypedHandlerFn:
    async def handler(
        command: MessageEnvelope, metadata: DeliveryMetadata, routing_key: str
    ) -> HandlerSuccess | HandlerFailedResult | HandlerRetryableError:
        return await instance_action(
            command, metadata, routing_key, settings=settings, expected_action=action
        )

    return handler
