"""OpenStack VM create handler with replay-safe provider marker."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from typing import Any

from openstack import exceptions as os_exc

from ops.application.credential_resolver import CpsResolutionError, CredentialResolver
from ops.application.handlers.registry import TypedHandlerFn
from ops.config import Settings
from ops.contracts.errors import CommonError, ErrorCategory
from ops.contracts.messages.delivery import DeliveryMetadata
from ops.contracts.messages.envelope import MessageEnvelope
from ops.contracts.messages.instance import InstanceAction, InstanceCommandPayload
from ops.contracts.messages.types import OPERATION_COMPLETED, OPERATION_FAILED, OPERATION_PROGRESS
from ops.messaging.consumer import HandlerFailedResult, HandlerRetryableError, HandlerSuccess
from ops.observability.redaction import redact_mapping
from ops.openstack.errors import normalize_openstack_exception
from ops.openstack.factory import openstack_connection
from ops.openstack.inventory import collect_instance_relationships, map_resource
from ops.openstack.waiter import (
    WaiterConfig,
    WaiterProviderError,
    WaiterTimeoutError,
    wait_for_state,
)

logger = logging.getLogger(__name__)


def _ssh_hosts(server: Any) -> list[str]:
    addresses = getattr(server, "addresses", {}) or {}
    hosts: list[str] = []
    for entries in addresses.values():
        for entry in entries or []:
            address = entry.get("addr") if isinstance(entry, dict) else None
            if address and address not in hosts:
                hosts.append(str(address))
    return hosts


def _event(
    command: MessageEnvelope, message_type: str, payload: dict[str, Any], label: str
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
            "payload": payload,
        }
    )
    return json.dumps(
        event.model_dump(mode="json", exclude_none=True), separators=(",", ":")
    ).encode()


def _failure(command: MessageEnvelope, error: CommonError) -> bytes:
    return _event(
        command, OPERATION_FAILED, {"error": error.model_dump(mode="json")}, "instance.failed"
    )


def _create_kwargs(
    payload: InstanceCommandPayload,
    operation_id: uuid.UUID,
    *,
    key_name: str | None = None,
) -> dict[str, Any]:
    if payload.create is None:
        raise ValueError("create payload is required")
    request = payload.create
    networks = [{"uuid": value} for value in request.network_provider_resource_ids]
    networks.extend({"port": value} for value in request.port_provider_resource_ids)
    kwargs: dict[str, Any] = {
        "name": request.name,
        "flavor_id": request.flavor_provider_resource_id,
        "networks": networks,
        "security_groups": request.security_group_provider_resource_ids,
        "key_name": key_name or request.key_name,
        "availability_zone": request.availability_zone,
        "config_drive": request.config_drive,
        "metadata": {
            **request.metadata,
            "cmp_operation_id": str(operation_id),
            "cmp_ssh_username": request.ssh_username,
            **({"cmp_keypair_name": key_name} if key_name else {}),
        },
    }
    if request.user_data is not None:
        kwargs["user_data"] = base64.b64encode(request.user_data.encode()).decode()
    if request.boot_source.value == "IMAGE":
        kwargs["image_id"] = request.image_provider_resource_id
    else:
        kwargs["block_device_mapping_v2"] = [
            {
                "uuid": request.image_provider_resource_id,
                "source_type": "image",
                "destination_type": "volume",
                "boot_index": 0,
                "volume_size": request.root_volume_size_gib,
                "delete_on_termination": request.delete_on_termination,
            }
        ]
    return {key: value for key, value in kwargs.items() if value is not None}


async def instance_create(
    command: MessageEnvelope, _metadata: DeliveryMetadata, _routing_key: str, *, settings: Settings
) -> HandlerSuccess | HandlerFailedResult | HandlerRetryableError:
    try:
        payload = InstanceCommandPayload.model_validate(command.payload)
    except ValueError:
        return HandlerFailedResult()
    if payload.action is not InstanceAction.CREATE:
        return HandlerFailedResult()
    request = payload.create
    if request is None:
        return HandlerFailedResult()
    try:
        resolution = await CredentialResolver(settings).resolve(command.provider_connection_id)
        with openstack_connection(resolution, settings) as connection:
            compute = connection.compute
            managed_key_name: str | None = None
            if request.ssh_public_key and not request.key_name:
                managed_key_name = f"cmp-{command.operation_id}"
                keypair = await asyncio.to_thread(
                    compute.find_keypair, managed_key_name, ignore_missing=True
                )
                if keypair is None:
                    await asyncio.to_thread(
                        compute.create_keypair,
                        name=managed_key_name,
                        public_key=request.ssh_public_key,
                    )
            existing = await asyncio.to_thread(
                compute.find_server, f"cmp-operation-{command.operation_id}", ignore_missing=True
            )
            if existing is None:
                try:
                    existing = await asyncio.to_thread(
                        compute.create_server,
                        **_create_kwargs(payload, command.operation_id, key_name=managed_key_name),
                    )
                except os_exc.EndpointNotFound:
                    # Nova may commit the server before the SDK loses the response;
                    # reconcile by the idempotency marker before reporting failure.
                    existing = await asyncio.to_thread(
                        compute.find_server,
                        f"cmp-operation-{command.operation_id}",
                        ignore_missing=True,
                    )
                    if existing is None:
                        raise
            progress = _event(
                command,
                OPERATION_PROGRESS,
                {"progress": 20, "state": "RUNNING", "message": "instance create started"},
                "instance.progress.running",
            )
            try:
                instance = await wait_for_state(
                    lambda: asyncio.to_thread(compute.get_server, existing.id),
                    config=WaiterConfig(target_states=frozenset({"ACTIVE", "SHUTOFF"})),
                )
            except (os_exc.EndpointNotFound, WaiterProviderError):
                # Some dev clouds expose a stale compute endpoint after create;
                # retain Nova's authoritative create response for reconciliation.
                try:
                    instance = await asyncio.to_thread(compute.get_server, existing.id)
                except os_exc.SDKException:
                    instance = existing
                if str(getattr(instance, "status", "")).upper() not in {"ACTIVE", "SHUTOFF"}:
                    raise
            try:
                ports, volumes = await asyncio.to_thread(
                    collect_instance_relationships, connection, str(instance.id)
                )
            except os_exc.SDKException:
                ports, volumes = [], []
            floating_ip: str | None = None
            floating_ip_id: str | None = None
            if request.floating_network_provider_resource_id:
                marker = f"cmp-operation-{command.operation_id}"
                floating = next(
                    (
                        item
                        for item in await asyncio.to_thread(lambda: list(connection.network.ips()))
                        if getattr(item, "description", None) == marker
                    ),
                    None,
                )
                if floating is None:
                    floating = await asyncio.to_thread(
                        connection.network.create_ip,
                        floating_network_id=request.floating_network_provider_resource_id,
                        description=marker,
                    )
                if floating is None:
                    raise RuntimeError("floating IP allocation did not return a resource")
                if not getattr(floating, "port_id", None):
                    await asyncio.to_thread(
                        compute.add_floating_ip_to_server,
                        instance,
                        floating.floating_ip_address,
                    )
                floating_ip = str(floating.floating_ip_address)
                floating_ip_id = str(floating.id)
            ssh_hosts = _ssh_hosts(instance)
            access_hosts = ([floating_ip] if floating_ip is not None else []) + ssh_hosts
            result = {
                "action": InstanceAction.CREATE.value,
                "instance": map_resource("instance", instance),
                "ports": ports,
                "volumes": volumes,
                "access": {
                    "ssh": {
                        "username": request.ssh_username,
                        "port": 22,
                        "key_name": managed_key_name or request.key_name,
                        "host": floating_ip or (ssh_hosts[0] if ssh_hosts else None),
                        "hosts": access_hosts,
                        "floating_ip_id": floating_ip_id,
                    }
                },
            }
            completed = _event(
                command, OPERATION_COMPLETED, {"result": result}, "instance.completed"
            )
            return HandlerSuccess(
                result_messages=((OPERATION_PROGRESS, progress), (OPERATION_COMPLETED, completed))
            )
    except CpsResolutionError as exc:
        return (
            HandlerRetryableError(retry_reason="CPS_UNAVAILABLE")
            if exc.retryable
            else HandlerFailedResult()
        )
    except (WaiterTimeoutError, WaiterProviderError) as exc:
        error = CommonError(
            code="PROVIDER_TIMEOUT" if isinstance(exc, WaiterTimeoutError) else "PROVIDER_CONFLICT",
            message="Provider instance did not reach the requested state",
            category=ErrorCategory.TIMEOUT
            if isinstance(exc, WaiterTimeoutError)
            else ErrorCategory.CONFLICT,
            retryable=isinstance(exc, WaiterTimeoutError),
        )
        return (
            HandlerRetryableError(retry_reason="PROVIDER_TIMEOUT")
            if error.retryable
            else HandlerFailedResult(
                result_routing_key=OPERATION_FAILED, result_body=_failure(command, error)
            )
        )
    except Exception as exc:
        logger.warning("instance create failed", extra={"error_type": type(exc).__name__})
        error = normalize_openstack_exception(exc, service="compute")
        if error.retryable:
            return HandlerRetryableError(error=error, retry_reason="PROVIDER_UNAVAILABLE")
        return HandlerFailedResult(
            result_routing_key=OPERATION_FAILED, result_body=_failure(command, error)
        )


def make_instance_create(settings: Settings) -> TypedHandlerFn:
    async def handler(
        command: MessageEnvelope, metadata: DeliveryMetadata, routing_key: str
    ) -> HandlerSuccess | HandlerFailedResult | HandlerRetryableError:
        return await instance_create(command, metadata, routing_key, settings=settings)

    return handler
