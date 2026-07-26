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
                resource_payload = _resource_payload(request, resource, request.resource_type)
                result["resource"] = resource_payload
                provider_resource_id = getattr(resource, "id", None)
                if provider_resource_id is None and isinstance(resource_payload, dict):
                    provider_resource_id = resource_payload.get("provider_resource_id")
                if provider_resource_id is not None:
                    result["provider_resource_id"] = str(provider_resource_id)
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
    operation = request.operation.lower()
    resource_type = request.resource_type.lower().replace("identity.", "").replace("network.", "")
    params = dict(request.parameters)
    provider_id = request.provider_resource_id
    if resource_type in {
        "network",
        "subnet",
        "router",
        "port",
        "security_group",
        "security_group_rule",
        "floating_ip",
        "floatingip",
        "security",
        "security_rule",
        "router_interface",
        "security.group",
        "security.group.rule",
        "router.interface",
    }:
        return _execute_network(connection, resource_type, operation, provider_id, params)
    identity = connection.identity
    if resource_type in {"domain", "project"}:
        getter = getattr(identity, f"get_{resource_type}")
        creator = getattr(identity, f"create_{resource_type}")
        if operation == "create":
            name = str(params["name"])
            # A display-name match is not an ownership proof.  CPS owns the
            # binding and must supply the provider resource ID for replay of
            # an already-created object; inventory/name lookup must never
            # silently adopt an unbound Keystone object.
            if provider_id:
                existing = getter(provider_id)
                if str(getattr(existing, "name", "")) != name:
                    raise os_exc.ConflictException(
                        f"provider resource {provider_id} has a different name"
                    )
                return existing, ResourceOperationState.SUCCEEDED
            for existing in (
                identity.domains(name=name)
                if resource_type == "domain"
                else identity.projects(name=name)
            ):
                if existing is not None:
                    raise os_exc.ConflictException(
                        "provider resource name is already used by an unbound object"
                    )
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


def _network_proxy(connection: Any) -> Any:
    proxy = getattr(connection, "network", None)
    if proxy is None:
        raise ValueError("network service is unavailable")
    return proxy


def _network_type(resource_type: str) -> str:
    return {
        "floatingip": "floating_ip",
        "security": "security_group",
        "security_rule": "security_group_rule",
        "security.group": "security_group",
        "security.group.rule": "security_group_rule",
        "router.interface": "router_interface",
    }.get(resource_type, resource_type)


def _network_owner_check(
    connection: Any, params: dict[str, Any], resource: Any | None = None
) -> None:
    """Prevent a project-scoped credential from mutating another tenant's resource."""
    effective = discover_effective_scope(connection)
    project_id = effective.get("project_id")
    requested = params.get("project_id") or params.get("tenant_id")
    if project_id and requested and str(project_id) != str(requested):
        raise ValueError("PROJECT_OWNERSHIP_MISMATCH")
    if project_id and resource is not None:
        owner = getattr(resource, "project_id", None) or getattr(resource, "tenant_id", None)
        if owner and str(owner) != str(project_id):
            raise ValueError("PROJECT_OWNERSHIP_MISMATCH")


def _find_network_existing(proxy: Any, kind: str, params: dict[str, Any]) -> Any | None:
    name = params.get("name")
    if not name:
        return None
    method = getattr(proxy, f"{kind}s", None)
    if not callable(method):
        return None
    filters = {"name": name}
    if params.get("project_id"):
        filters["project_id"] = params["project_id"]
    try:
        return next(iter(method(**filters)), None)
    except TypeError:
        return next((item for item in method() if getattr(item, "name", None) == name), None)


def _execute_network(
    connection: Any,
    resource_type: str,
    operation: str,
    provider_id: str | None,
    params: dict[str, Any],
) -> tuple[Any | None, ResourceOperationState]:
    proxy = _network_proxy(connection)
    kind = _network_type(resource_type)
    operation = operation.lower()
    _validate_network_parameters(kind, operation, params)
    if kind == "router_interface":
        router_id = str(params.get("router_id") or provider_id or "")
        if not router_id:
            raise ValueError("router_id is required")
        relation = {k: v for k, v in params.items() if k in {"subnet_id", "port_id"} and v}
        if not relation:
            raise ValueError("subnet_id or port_id is required")
        router = proxy.get_router(router_id)
        method_name = (
            "add_interface_to_router"
            if operation in {"ensure", "create", "add"}
            else "remove_interface_from_router"
        )
        method = getattr(proxy, method_name, None)
        if not callable(method):
            raise ValueError("router interface operation unsupported")
        try:
            result = method(router, **relation)
        except (os_exc.ConflictException, os_exc.BadRequestException):
            if operation in {"ensure", "create", "add"}:
                return router, ResourceOperationState.SUCCEEDED
            raise
        except (os_exc.NotFoundException, os_exc.ResourceNotFound):
            if operation in {"remove", "delete"}:
                return None, ResourceOperationState.ALREADY_ABSENT
            raise
        return result or router, ResourceOperationState.SUCCEEDED

    if kind == "floating_ip":
        if operation in {"allocate", "create", "ensure"}:
            network_id = params.get("floating_network_id") or params.get("network_id")
            if not network_id:
                raise ValueError("floating_network_id is required")
            _network_owner_check(connection, params)
            existing = next(
                (
                    item
                    for item in proxy.ips()
                    if getattr(item, "floating_network_id", None) == network_id
                    and not getattr(item, "port_id", None)
                ),
                None,
            )
            if existing is not None:
                return existing, ResourceOperationState.SUCCEEDED
            return proxy.create_ip(
                floating_network_id=network_id,
                **{
                    k: v
                    for k, v in params.items()
                    if k not in {"floating_network_id", "network_id"}
                },
            ), ResourceOperationState.SUCCEEDED
        if not provider_id:
            raise ValueError("provider_resource_id is required")
        try:
            existing = proxy.get_ip(provider_id)
        except os_exc.NotFoundException:
            return (
                (None, ResourceOperationState.ALREADY_ABSENT)
                if operation in {"release", "delete"}
                else (_raise("floating IP not found"))
            )
        _network_owner_check(connection, params, existing)
        if operation in {"associate", "disassociate", "update", "patch"}:
            values = (
                {"port_id": params.get("port_id")}
                if operation != "disassociate"
                else {"port_id": None}
            )
            return proxy.update_ip(existing, **values), ResourceOperationState.SUCCEEDED
        if operation in {"release", "delete"}:
            proxy.delete_ip(existing, ignore_missing=True)
            return None, ResourceOperationState.SUCCEEDED

    creator = getattr(proxy, f"create_{kind}", None)
    getter = getattr(proxy, f"get_{kind}", None)
    updater = getattr(proxy, f"update_{kind}", None)
    deleter = getattr(proxy, f"delete_{kind}", None)
    if operation in {"create", "ensure"}:
        _network_owner_check(connection, params)
        existing = _find_network_existing(proxy, kind, params) if operation == "ensure" else None
        if existing is not None:
            return existing, ResourceOperationState.SUCCEEDED
        if not callable(creator):
            raise ValueError(f"{kind} create unsupported")
        return creator(**params), ResourceOperationState.SUCCEEDED
    if not provider_id or not callable(getter):
        raise ValueError("provider_resource_id is required")
    try:
        existing = getter(provider_id)
    except (os_exc.NotFoundException, os_exc.ResourceNotFound):
        if operation in {"delete", "release", "remove"}:
            return None, ResourceOperationState.ALREADY_ABSENT
        raise
    _network_owner_check(connection, params, existing)
    if operation in {"get", "collect"}:
        return existing, ResourceOperationState.SUCCEEDED
    if operation in {"update", "patch"}:
        if not callable(updater):
            raise ValueError(f"{kind} update unsupported")
        return updater(existing, **params), ResourceOperationState.SUCCEEDED
    if operation in {"delete", "release", "remove"}:
        if not callable(deleter):
            raise ValueError(f"{kind} delete unsupported")
        deleter(existing, ignore_missing=True)
        return None, ResourceOperationState.SUCCEEDED
    raise ValueError(f"unsupported network operation: {kind}/{operation}")


def _validate_network_parameters(kind: str, operation: str, params: dict[str, Any]) -> None:
    """Reject malformed Neutron requests before invoking an SDK mutation."""
    if operation not in {"create", "ensure", "update", "patch"}:
        return
    if kind in {"subnet", "port"} and not params.get("network_id"):
        raise ValueError("network_id is required")
    if kind == "subnet" and not params.get("cidr"):
        raise ValueError("cidr is required")
    if kind == "security_group_rule":
        if not params.get("security_group_id"):
            raise ValueError("security_group_id is required")
        direction = params.get("direction")
        if direction not in {"ingress", "egress"}:
            raise ValueError("direction must be ingress or egress")
        ethertype = params.get("ethertype")
        if ethertype is not None and ethertype not in {"IPv4", "IPv6"}:
            raise ValueError("ethertype must be IPv4 or IPv6")
        minimum = params.get("port_range_min")
        maximum = params.get("port_range_max")
        if (minimum is None) != (maximum is None):
            raise ValueError("port range requires both minimum and maximum")
        if minimum is not None and (
            not isinstance(minimum, int)
            or not isinstance(maximum, int)
            or minimum < 1
            or maximum > 65535
            or minimum > maximum
        ):
            raise ValueError("invalid port range")
        protocol = params.get("protocol")
        if protocol is not None and not isinstance(protocol, str):
            raise ValueError("protocol must be a string")


def _raise(message: str) -> Any:
    raise ValueError(message)


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
