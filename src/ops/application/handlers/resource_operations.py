"""Replay-safe identity, assignment, and quota operations.

The handler intentionally uses only OpenStackSDK proxy methods.  CPS owns
durability and idempotency; OPS makes the provider mutation converge to the
requested state and emits a normalized resource-operation result.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from openstack import exceptions as os_exc

from ops.application.credential_resolver import CpsResolutionError, CredentialResolver
from ops.application.handlers.registry import TypedHandlerFn
from ops.config import Settings
from ops.contracts.messages.delivery import DeliveryMetadata
from ops.contracts.messages.envelope import MessageEnvelope
from ops.contracts.messages.resource_operations import (
    ResourceOperationRequest,
    ResourceOperationState,
)
from ops.contracts.messages.types import OPERATION_COMPLETED, OPERATION_FAILED
from ops.messaging.consumer import HandlerFailedResult, HandlerRetryableError, HandlerSuccess
from ops.observability.redaction import redact_mapping
from ops.openstack.errors import normalize_openstack_exception
from ops.openstack.factory import openstack_connection
from ops.openstack.inventory import map_resource
from ops.openstack.scope import ScopeKind, discover_effective_scope


def _event(
    command: MessageEnvelope,
    payload: dict[str, Any],
    label: str,
    message_type: str = OPERATION_COMPLETED,
) -> bytes:
    body = payload if message_type == OPERATION_COMPLETED else {"error": payload}
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
            "payload": body,
        }
    )
    return json.dumps(
        event.model_dump(mode="json", exclude_none=True), separators=(",", ":")
    ).encode()


def _request(command: MessageEnvelope) -> ResourceOperationRequest:
    payload = dict(command.payload)
    payload.setdefault("operation_id", command.operation_id)
    payload.setdefault("provider_connection_id", command.provider_connection_id)
    request = ResourceOperationRequest.model_validate(payload)
    if _contains_secret(request.parameters):
        raise ValueError("secret parameters are not accepted")
    return request


def _contains_secret(value: Any) -> bool:
    if isinstance(value, dict):
        sensitive = ("password", "token", "authorization", "private_key", "user_data")
        return any(
            any(part in str(key).lower() for part in sensitive) or _contains_secret(child)
            for key, child in value.items()
        )
    if isinstance(value, list | tuple):
        return any(_contains_secret(item) for item in value)
    return False


def _scope_allowed(connection: Any, required: str) -> bool:
    if required == ScopeKind.UNKNOWN.value:
        return False
    effective = discover_effective_scope(connection)["scope_kind"]
    order = {ScopeKind.PROJECT.value: 1, ScopeKind.DOMAIN.value: 2, ScopeKind.SYSTEM.value: 3}
    return effective in order and order[effective] >= order.get(required, 99)


def _resource_payload(
    request: ResourceOperationRequest, resource: Any, resource_type: str
) -> dict[str, Any]:
    if isinstance(resource, dict):
        return resource
    if isinstance(resource, list):
        return {"items": resource}
    return map_resource(resource_type, resource)


async def resource_operation(
    command: MessageEnvelope,
    _metadata: DeliveryMetadata,
    _routing_key: str,
    *,
    settings: Settings,
) -> HandlerSuccess | HandlerFailedResult | HandlerRetryableError:
    try:
        request = _request(command)
        if command.credential_reference is None:
            return HandlerFailedResult()
        resolution = await CredentialResolver(settings).resolve(
            command.credential_reference, command.provider_connection_id
        )
        with openstack_connection(resolution, settings) as connection:
            if not _scope_allowed(connection, request.required_scope.value):
                result = request.model_dump(mode="json") | {
                    "state": ResourceOperationState.UNSUPPORTED.value,
                    "error": {
                        "code": "SCOPE_INSUFFICIENT",
                        "message": "effective provider scope is insufficient",
                    },
                }
                return HandlerSuccess(
                    result_messages=(
                        (
                            OPERATION_COMPLETED,
                            _event(command, result, "resource.operation.unsupported"),
                        ),
                    )
                )
            resource, state = await asyncio.to_thread(_execute, connection, request)
            result = request.model_dump(mode="json") | {"state": state.value}
            if resource is not None:
                result["resource"] = _resource_payload(request, resource, request.resource_type)
            return HandlerSuccess(
                result_messages=(
                    (OPERATION_COMPLETED, _event(command, result, "resource.operation.completed")),
                )
            )
    except CpsResolutionError as exc:
        return (
            HandlerRetryableError(retry_reason="CPS_UNAVAILABLE")
            if exc.retryable
            else HandlerFailedResult()
        )
    except Exception as exc:
        error = normalize_openstack_exception(exc, service="identity")
        if error.retryable:
            return HandlerRetryableError(error=error, retry_reason="PROVIDER_UNAVAILABLE")
        return HandlerFailedResult(
            result_routing_key=OPERATION_FAILED,
            result_body=_event(
                command,
                error.model_dump(mode="json"),
                "resource.operation.failed",
                OPERATION_FAILED,
            ),
        )


def _execute(
    connection: Any, request: ResourceOperationRequest
) -> tuple[Any | None, ResourceOperationState]:
    identity = connection.identity
    operation = request.operation.lower()
    resource_type = request.resource_type.lower().replace("identity.", "")
    params = dict(request.parameters)
    provider_id = request.provider_resource_id
    if resource_type in {"domain", "project"}:
        getter = getattr(identity, f"get_{resource_type}")
        creator = getattr(identity, f"create_{resource_type}")
        if operation == "create":
            name = str(params["name"])
            existing = next(
                iter(
                    identity.domains(name=name)
                    if resource_type == "domain"
                    else identity.projects(name=name)
                ),
                None,
            )
            if existing is not None:
                return existing, ResourceOperationState.SUCCEEDED
            return creator(**params), ResourceOperationState.SUCCEEDED
        if not provider_id:
            raise ValueError("provider_resource_id is required")
        try:
            existing = getter(provider_id)
        except (os_exc.ResourceNotFound, os_exc.NotFoundException):
            if operation == "delete":
                return None, ResourceOperationState.ALREADY_ABSENT
            raise
        if operation in {"update", "patch"}:
            updater = getattr(identity, f"update_{resource_type}")
            return updater(existing, **params), ResourceOperationState.SUCCEEDED
        if operation == "delete":
            deleter = getattr(identity, f"delete_{resource_type}")
            deleter(existing, ignore_missing=True)
            return None, ResourceOperationState.SUCCEEDED
    if resource_type == "role" and operation == "collect":
        return [
            {
                "provider_resource_id": str(getattr(item, "id", "")),
                "name": str(getattr(item, "name", getattr(item, "id", ""))),
                "attributes": {"is_enabled": getattr(item, "is_enabled", None)},
            }
            for item in identity.roles()
        ], ResourceOperationState.SUCCEEDED
    if resource_type in {"role_assignment", "assignment"}:
        role = params["role"]
        user = params.get("user")
        project = params.get("project")
        domain = params.get("domain")
        if operation == "collect":
            assignments = identity.role_assignments(
                role=role, user=user, project=project, domain=domain
            )
            return [
                {
                    "provider_resource_id": str(getattr(item, "id", "")),
                    "name": str(getattr(item, "id", "")),
                    "attributes": {
                        key: value
                        for key in ("role_id", "user_id", "group_id", "project_id", "domain_id")
                        if (value := getattr(item, key, None)) is not None
                    },
                }
                for item in assignments
            ], ResourceOperationState.SUCCEEDED
        if operation in {"ensure", "assign", "create"}:
            kwargs = {"role": role, "user": user, "project": project, "domain": domain}
            kwargs = {key: value for key, value in kwargs.items() if value is not None}
            existing = next(iter(identity.role_assignments(**kwargs)), None)
            if existing is not None:
                return existing, ResourceOperationState.SUCCEEDED
            return identity.create_role_assignment(**kwargs), ResourceOperationState.SUCCEEDED
        if operation in {"revoke", "delete"}:
            identity.delete_role_assignment(role=role, user=user, project=project, domain=domain)
            return None, ResourceOperationState.SUCCEEDED
    if resource_type in {
        "quota",
        "project_quota",
        "compute_quota",
        "network_quota",
        "block_storage_quota",
    }:
        project_id = provider_id or params.get("project_id")
        if not project_id:
            raise ValueError("project_id is required")
        service = params.get("service") or {
            "compute_quota": "compute",
            "network_quota": "network",
            "block_storage_quota": "block_storage",
        }.get(resource_type, "compute")
        proxy = {
            "compute": connection.compute,
            "network": connection.network,
            "block_storage": connection.block_storage,
        }[service]
        getter = getattr(proxy, "get_quota_set", None)
        if not callable(getter):
            getter = getattr(proxy, "get_quota", None)
        if not callable(getter):
            raise ValueError(f"quota getter unsupported for {service}")
        quota = getter(project_id)
        if operation in {"update", "set"}:
            values = {
                key: value for key, value in params.items() if key not in {"project_id", "service"}
            }
            updater = getattr(proxy, "update_quota_set", None)
            if not callable(updater):
                updater = getattr(proxy, "update_quota", None)
            if not callable(updater):
                raise ValueError(f"quota updater unsupported for {service}")
            quota = updater(project_id, **values)
        return {
            "provider_resource_id": str(project_id),
            "name": str(project_id),
            "attributes": _normalize_quota(quota),
        }, ResourceOperationState.SUCCEEDED
    raise ValueError(f"unsupported resource operation: {request.resource_type}/{request.operation}")


def _normalize_quota(quota: Any) -> dict[str, Any]:
    values = getattr(quota, "to_dict", lambda: vars(quota))()
    return {
        str(key): (None if value == -1 else value)
        for key, value in values.items()
        if not str(key).startswith("_")
    }


def make_resource_operation(settings: Settings) -> TypedHandlerFn:
    async def handler(
        command: MessageEnvelope, metadata: DeliveryMetadata, routing_key: str
    ) -> HandlerSuccess | HandlerFailedResult | HandlerRetryableError:
        return await resource_operation(command, metadata, routing_key, settings=settings)

    return handler
