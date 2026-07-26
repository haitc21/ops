"""OPS inventory collection coordinator and confirmed-event payload builder."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from keystoneauth1 import exceptions as ks_exc
from openstack import exceptions as os_exc

from ops.application.credential_resolver import CpsResolutionError, CredentialResolver
from ops.application.handlers.registry import TypedHandlerFn
from ops.config import Settings
from ops.contracts.messages.delivery import DeliveryMetadata
from ops.contracts.messages.envelope import MessageEnvelope
from ops.contracts.messages.inventory import (
    InventoryBatchItem,
    InventoryBatchPayload,
    compute_inventory_checksum,
)
from ops.contracts.messages.types import INVENTORY_BATCH, INVENTORY_COMPLETED
from ops.messaging.consumer import HandlerFailedResult, HandlerRetryableError, HandlerSuccess
from ops.observability.redaction import redact_mapping
from ops.openstack.factory import openstack_connection
from ops.openstack.inventory import COLLECTIONS, collect_resources, collect_targeted_resource

logger = logging.getLogger(__name__)


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


def build_inventory_batch_messages(
    command: MessageEnvelope,
    *,
    sync_id: uuid.UUID,
    resource_type: str,
    items: list[dict[str, Any]],
    batch_size: int = 100,
    collection_status: str = "COMPLETE",
) -> tuple[tuple[str, bytes], ...]:
    if resource_type not in COLLECTIONS or batch_size < 1:
        raise ValueError("invalid inventory collection or batch size")
    messages: list[tuple[str, bytes]] = []
    chunks = [items[index : index + batch_size] for index in range(0, len(items), batch_size)] or [
        []
    ]
    for sequence, chunk in enumerate(chunks, start=1):
        payload = InventoryBatchPayload.model_validate(
            {
                "sync_id": str(sync_id),
                "resource_type": resource_type,
                "sequence": sequence,
                "is_last": sequence == len(chunks),
                "collection_status": collection_status,
                "item_count": len(chunk),
                "checksum": compute_inventory_checksum(
                    [
                        InventoryBatchItem.model_validate(item).model_dump(
                            mode="json", exclude_none=True, exclude_defaults=True
                        )
                        for item in chunk
                    ]
                ),
                "items": chunk,
            }
        )
        body = _event(
            command,
            INVENTORY_BATCH,
            payload.model_dump(mode="json"),
            f"inventory.batch.{resource_type}.{sequence}",
        )
        messages.append((INVENTORY_BATCH, body))
    return tuple(messages)


async def inventory_collect(
    command: MessageEnvelope,
    _metadata: DeliveryMetadata,
    _routing_key: str,
    *,
    settings: Settings,
) -> HandlerSuccess | HandlerFailedResult | HandlerRetryableError:
    sync_id_raw = command.payload.get("sync_id")
    collections = command.payload.get("collections", list(COLLECTIONS))
    batch_size = command.payload.get("batch_size", 100)
    if not isinstance(sync_id_raw, str):
        return HandlerFailedResult()
    if not isinstance(collections, list) or not all(item in COLLECTIONS for item in collections):
        return HandlerFailedResult()
    try:
        sync_id = uuid.UUID(sync_id_raw)
        batch_size_value = int(batch_size)
    except (TypeError, ValueError):
        return HandlerFailedResult()
    try:
        resolution = await CredentialResolver(settings).resolve(command.provider_connection_id)
        with openstack_connection(resolution, settings) as connection:
            results: list[tuple[str, bytes]] = []
            for resource_type in collections:
                try:
                    items = await asyncio.to_thread(collect_resources, connection, resource_type)
                    collection_status = "COMPLETE"
                except (
                    AttributeError,
                    os_exc.ForbiddenException,
                    ks_exc.AuthorizationFailure,
                    os_exc.EndpointNotFound,
                    ks_exc.catalog.EndpointNotFound,
                    os_exc.ServiceDisabledException,
                    os_exc.ServiceDiscoveryException,
                ):
                    items = []
                    collection_status = "SKIPPED_UNSUPPORTED"
                results.extend(
                    build_inventory_batch_messages(
                        command,
                        sync_id=sync_id,
                        resource_type=resource_type,
                        items=items,
                        batch_size=batch_size_value,
                        collection_status=collection_status,
                    )
                )
        completed = _event(
            command,
            INVENTORY_COMPLETED,
            {"sync_id": str(sync_id), "collections": collections, "status": "SUCCEEDED"},
            "inventory.completed",
        )
        results.append((INVENTORY_COMPLETED, completed))
        return HandlerSuccess(result_messages=tuple(results))
    except CpsResolutionError as exc:
        if exc.retryable:
            return HandlerRetryableError(retry_reason="CPS_UNAVAILABLE")
        return HandlerFailedResult()
    except Exception as exc:
        logger.warning("inventory collection failed", extra={"error_type": type(exc).__name__})
        return HandlerRetryableError(retry_reason="PROVIDER_UNAVAILABLE")


async def inventory_refresh(
    command: MessageEnvelope,
    _metadata: DeliveryMetadata,
    _routing_key: str,
    *,
    settings: Settings,
) -> HandlerSuccess | HandlerFailedResult | HandlerRetryableError:
    sync_id_raw = command.payload.get("sync_id")
    resource_type = command.payload.get("resource_type")
    provider_resource_id = command.payload.get("provider_resource_id")
    if (
        not isinstance(sync_id_raw, str)
        or resource_type not in COLLECTIONS
        or not isinstance(provider_resource_id, str)
        or not provider_resource_id
    ):
        return HandlerFailedResult()
    try:
        sync_id = uuid.UUID(sync_id_raw)
        resolution = await CredentialResolver(settings).resolve(command.provider_connection_id)
        with openstack_connection(resolution, settings) as connection:
            try:
                item = await asyncio.to_thread(
                    collect_targeted_resource, connection, resource_type, provider_resource_id
                )
            except (os_exc.ResourceNotFound, os_exc.NotFoundException):
                item = {
                    "provider_resource_id": provider_resource_id,
                    "name": provider_resource_id,
                    "lifecycle_state": "DELETED",
                    "attributes": {},
                }
            messages = list(
                build_inventory_batch_messages(
                    command,
                    sync_id=sync_id,
                    resource_type=resource_type,
                    items=[item],
                    batch_size=1,
                )
            )
        messages.append(
            (
                INVENTORY_COMPLETED,
                _event(
                    command,
                    INVENTORY_COMPLETED,
                    {
                        "sync_id": str(sync_id),
                        "collections": [resource_type],
                        "status": "SUCCEEDED",
                    },
                    "inventory.completed",
                ),
            )
        )
        return HandlerSuccess(result_messages=tuple(messages))
    except CpsResolutionError as exc:
        if exc.retryable:
            return HandlerRetryableError(retry_reason="CPS_UNAVAILABLE")
        return HandlerFailedResult()
    except Exception as exc:
        logger.warning("inventory refresh failed", extra={"error_type": type(exc).__name__})
        return HandlerRetryableError(retry_reason="PROVIDER_UNAVAILABLE")


def make_inventory_collect(settings: Settings) -> TypedHandlerFn:
    async def handler(
        command: MessageEnvelope, metadata: DeliveryMetadata, routing_key: str
    ) -> HandlerSuccess | HandlerFailedResult | HandlerRetryableError:
        return await inventory_collect(command, metadata, routing_key, settings=settings)

    return handler


def make_inventory_refresh(settings: Settings) -> TypedHandlerFn:
    async def handler(
        command: MessageEnvelope, metadata: DeliveryMetadata, routing_key: str
    ) -> HandlerSuccess | HandlerFailedResult | HandlerRetryableError:
        return await inventory_refresh(command, metadata, routing_key, settings=settings)

    return handler
