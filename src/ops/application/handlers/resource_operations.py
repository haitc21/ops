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
from ops.contracts.errors import CommonError, ErrorCategory
from ops.contracts.messages.delivery import DeliveryMetadata
from ops.contracts.messages.envelope import MessageEnvelope
from ops.contracts.messages.resource_operations import (
    ResourceOperationRequest,
    ResourceOperationState,
)
from ops.contracts.messages.types import OPERATION_COMPLETED, OPERATION_FAILED, OPERATION_PROGRESS
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
    body = (
        payload
        if message_type == OPERATION_PROGRESS
        else {"result": payload}
        if message_type == OPERATION_COMPLETED
        else {"error": payload}
    )
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


def _highest_admin_role(identity: Any) -> Any:
    """Select the provider's strongest administrative role deterministically."""
    roles = list(identity.roles())
    ranked = {
        "admin": 100,
        "cloud_admin": 90,
        "administrator": 80,
    }
    candidates = [role for role in roles if str(getattr(role, "name", "")).lower() in ranked]
    if not candidates:
        raise ValueError("OpenStack provider has no administrative role")
    return max(candidates, key=lambda role: ranked[str(role.name).lower()])


def _ensure_creator_role(
    identity: Any, resource_type: str, resource: Any, username: str
) -> dict[str, str]:
    """Grant the creating provider user admin at the newly-created scope."""
    user = identity.find_user(username, ignore_missing=True)
    if user is None:
        raise ValueError(f"OpenStack provider user {username!r} was not found")
    role = _highest_admin_role(identity)
    scope_key = "domain" if resource_type == "domain" else "project"
    assignment = {
        "role": str(role.id),
        "user": str(user.id),
        scope_key: str(resource.id),
    }
    validator_name = f"validate_user_has_{scope_key}_role"
    assigner_name = f"assign_{scope_key}_role_to_user"
    validator = getattr(identity, validator_name, None)
    assigner = getattr(identity, assigner_name, None)
    if callable(validator) and callable(assigner):
        if not validator(resource.id, user.id, role.id):
            assigner(resource.id, user.id, role.id)
    elif next(iter(identity.role_assignments(**assignment)), None) is None:
        # Compatibility for proxy doubles and older SDK adapters.
        identity.create_role_assignment(**assignment)
    return {
        "user_id": str(user.id),
        "role_id": str(role.id),
        "role_name": str(role.name),
        "scope_type": scope_key,
        "scope_id": str(resource.id),
    }


async def resource_operation(
    command: MessageEnvelope,
    _metadata: DeliveryMetadata,
    _routing_key: str,
    *,
    settings: Settings,
) -> HandlerSuccess | HandlerFailedResult | HandlerRetryableError:
    try:
        request = _request(command)
        resolution = await CredentialResolver(settings).resolve(command.provider_connection_id)
        with openstack_connection(resolution, settings) as connection:
            await asyncio.to_thread(connection.authorize)
            if not _scope_allowed(connection, request.required_scope.value):
                error = CommonError(
                    code="SCOPE_INSUFFICIENT",
                    message="effective provider scope is insufficient",
                    category=ErrorCategory.AUTHORIZATION,
                    retryable=False,
                )
                return HandlerFailedResult(
                    result_routing_key=OPERATION_FAILED,
                    result_body=_event(
                        command,
                        error.model_dump(mode="json"),
                        "resource.operation.scope-insufficient",
                        OPERATION_FAILED,
                    ),
                )
            resource, state = await asyncio.to_thread(
                _execute, connection, request, resolution.username
            )
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
                    (
                        OPERATION_PROGRESS,
                        _event(
                            command,
                            {
                                "progress": 10,
                                "state": "RUNNING",
                                "message": "resource operation started",
                            },
                            "resource.operation.started",
                            OPERATION_PROGRESS,
                        ),
                    ),
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
        error = normalize_openstack_exception(
            exc,
            service=(
                "block_storage"
                if "volume" in str(getattr(command, "payload", {}).get("resource_type", "")).lower()
                else "identity"
            ),
        )
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
    connection: Any, request: ResourceOperationRequest, creator_username: str | None = None
) -> tuple[Any | None, ResourceOperationState]:
    operation = request.operation.lower()
    resource_type = request.resource_type.lower().replace("identity.", "").replace("network.", "")
    params = dict(request.parameters)
    provider_id = request.provider_resource_id
    if resource_type == "volume":
        return _execute_volume(connection, operation, provider_id, request, params)
    if resource_type == "volume-attachment":
        return _execute_volume_attachment(connection, operation, params)
    if resource_type == "snapshot":
        return _execute_snapshot(connection, operation, provider_id, params)
    if resource_type in {
        "network",
        "subnet",
        "router",
        "port",
        "security_group",
        "security-group",
        "security_group_rule",
        "security-group-rule",
        "floating_ip",
        "floatingip",
        "floating-ip",
        "security",
        "security_rule",
        "router_interface",
        "router-interface",
        "security.group",
        "security.group.rule",
        "router.interface",
    }:
        return _execute_network(connection, resource_type, operation, provider_id, params)
    identity = connection.identity
    if resource_type in {"domain", "project"}:
        getter = getattr(identity, f"get_{resource_type}")
        creator = getattr(identity, f"create_{resource_type}")
        provider_params = {
            key: value
            for key, value in params.items()
            if key not in {"binding_id", "org_id", "workspace_id"}
        }
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
                if creator_username:
                    _ensure_creator_role(identity, resource_type, existing, creator_username)
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
            resource = creator(**provider_params)
            if creator_username:
                _ensure_creator_role(identity, resource_type, resource, creator_username)
            return resource, ResourceOperationState.SUCCEEDED
        if not provider_id:
            raise ValueError("provider_resource_id is required")
        try:
            existing = getter(provider_id)
        except (os_exc.ResourceNotFound, os_exc.NotFoundException):
            if operation == "delete":
                return None, ResourceOperationState.ALREADY_ABSENT
            raise
        if operation in {"update", "patch", "disable"}:
            updater = getattr(identity, f"update_{resource_type}")
            if operation == "disable":
                provider_params["enabled"] = False
            return updater(existing, **provider_params), ResourceOperationState.SUCCEEDED
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


def _execute_volume(
    connection: Any,
    operation: str,
    provider_id: str | None,
    request: Any,
    params: dict[str, Any],
) -> tuple[Any | None, ResourceOperationState]:
    """Execute a project-scoped Cinder volume lifecycle operation.

    The block-storage proxy owns provider I/O.  This helper deliberately does
    not force-delete attached volumes and treats a missing delete target as a
    converged, idempotent result.
    """
    proxy = getattr(connection, "block_storage", None)
    if proxy is None:
        raise ValueError("block_storage service is unavailable")

    def value(name: str, default: Any = None) -> Any:
        return getattr(request, name, params.get(name, default))

    if operation in {"create", "ensure"}:
        if operation == "ensure" and provider_id:
            return _get_volume(proxy, provider_id), ResourceOperationState.SUCCEEDED
        name = value("name")
        size = value("size_gib")
        source_snapshot_id = value("source_snapshot_provider_resource_id")
        if not name or (size is None and not source_snapshot_id):
            raise ValueError("volume create requires name and size_gib or source snapshot")
        create_params = {
            "name": name,
        }
        if size is not None:
            create_params["size"] = size
        if source_snapshot_id:
            snapshot = _get_snapshot(proxy, str(source_snapshot_id))
            _snapshot_owner_check(connection, params, snapshot)
            create_params["snapshot_id"] = str(source_snapshot_id)
        optional = {
            "volume_type": value("volume_type_provider_resource_id"),
            "availability_zone": value("availability_zone"),
            "metadata": value("metadata", {}),
            "project_id": value("project_provider_resource_id"),
        }
        create_params.update({key: item for key, item in optional.items() if item is not None})
        return proxy.create_volume(**create_params), ResourceOperationState.SUCCEEDED

    if not provider_id:
        raise ValueError(f"volume {operation} requires provider_resource_id")
    try:
        existing = _get_volume(proxy, provider_id)
    except (os_exc.NotFoundException, os_exc.ResourceNotFound):
        if operation == "delete":
            return None, ResourceOperationState.ALREADY_ABSENT
        raise

    if operation == "resize":
        requested_size = value("size_gib")
        if requested_size is None:
            raise ValueError("volume resize requires size_gib")
        current_size = getattr(existing, "size", None)
        if current_size is not None and requested_size < current_size:
            raise ValueError("volume resize cannot shrink")
        if current_size == requested_size:
            return existing, ResourceOperationState.SUCCEEDED
        result = proxy.extend_volume(existing, requested_size)
        return result or existing, ResourceOperationState.SUCCEEDED

    if operation == "delete":
        proxy.delete_volume(existing, ignore_missing=True)
        return None, ResourceOperationState.SUCCEEDED

    raise ValueError(f"unsupported volume operation: {operation}")


def _get_volume(proxy: Any, provider_id: str) -> Any:
    getter = getattr(proxy, "get_volume", None)
    if not callable(getter):
        raise ValueError("volume getter unsupported")
    return getter(provider_id)


def _execute_snapshot(
    connection: Any,
    operation: str,
    provider_id: str | None,
    params: dict[str, Any],
) -> tuple[Any | None, ResourceOperationState]:
    """Execute replay-safe Cinder snapshot lifecycle operations."""
    proxy = getattr(connection, "block_storage", None)
    if proxy is None:
        raise ValueError("block_storage service is unavailable")

    if operation in {"create", "ensure"}:
        volume_id = params.get("volume_id")
        if not volume_id:
            raise ValueError("snapshot create requires volume_id")
        volume = _get_volume(proxy, str(volume_id))
        _snapshot_owner_check(connection, params, volume)
        if operation == "ensure" and provider_id:
            return _get_snapshot(proxy, provider_id), ResourceOperationState.SUCCEEDED
        name = params.get("name")
        if not name:
            raise ValueError("snapshot create requires name")
        create_params = {
            key: params[key]
            for key in ("volume_id", "name", "description", "force", "metadata")
            if params.get(key) is not None
        }
        snapshot = proxy.create_snapshot(**create_params)
        waiter = getattr(proxy, "wait_for_status", None)
        if callable(waiter):
            snapshot = waiter(
                snapshot,
                status="available",
                failures=["error", "error_deleting"],
                interval=1,
                wait=300,
            )
        return snapshot, ResourceOperationState.SUCCEEDED

    if not provider_id:
        raise ValueError(f"snapshot {operation} requires provider_resource_id")
    try:
        existing = _get_snapshot(proxy, provider_id)
    except (os_exc.NotFoundException, os_exc.ResourceNotFound):
        if operation == "delete":
            return None, ResourceOperationState.ALREADY_ABSENT
        raise
    _snapshot_owner_check(connection, params, existing)

    if operation in {"update", "patch"}:
        update_params = {
            key: params[key]
            for key in ("name", "description", "metadata")
            if params.get(key) is not None
        }
        if not update_params:
            return existing, ResourceOperationState.SUCCEEDED
        return proxy.update_snapshot(existing, **update_params), ResourceOperationState.SUCCEEDED

    if operation == "delete":
        proxy.delete_snapshot(existing, ignore_missing=True, force=bool(params.get("force", False)))
        return None, ResourceOperationState.SUCCEEDED

    raise ValueError(f"unsupported snapshot operation: {operation}")


def _get_snapshot(proxy: Any, provider_id: str) -> Any:
    getter = getattr(proxy, "get_snapshot", None)
    if not callable(getter):
        raise ValueError("snapshot getter unsupported")
    return getter(provider_id)


def _snapshot_owner_check(connection: Any, params: dict[str, Any], resource: Any) -> None:
    effective_project = discover_effective_scope(connection).get("project_id")
    requested_project = params.get("project_id") or params.get("project_provider_resource_id")
    resource_project = getattr(resource, "project_id", None) or getattr(resource, "tenant_id", None)
    if effective_project and requested_project and str(effective_project) != str(requested_project):
        raise ValueError("PROJECT_OWNERSHIP_MISMATCH")
    if effective_project and resource_project and str(effective_project) != str(resource_project):
        raise ValueError("PROJECT_OWNERSHIP_MISMATCH")
    if requested_project and resource_project and str(requested_project) != str(resource_project):
        raise ValueError("PROJECT_OWNERSHIP_MISMATCH")


def _execute_volume_attachment(
    connection: Any,
    operation: str,
    params: dict[str, Any],
) -> tuple[Any | None, ResourceOperationState]:
    """Attach or detach a Cinder volume to a Nova server."""
    server_id = params.get("server_id")
    volume_id = params.get("volume_id")
    if not server_id or not volume_id:
        raise ValueError("volume attachment requires server_id and volume_id")

    compute = getattr(connection, "compute", None)
    if compute is None:
        raise ValueError("compute service is unavailable")
    block_storage = getattr(connection, "block_storage", None)
    if block_storage is None:
        raise ValueError("block storage service is unavailable")
    volume = block_storage.get_volume(volume_id)
    server = compute.get_server(server_id)
    volume_project = getattr(volume, "project_id", None) or getattr(volume, "tenant_id", None)
    server_project = getattr(server, "project_id", None) or getattr(server, "tenant_id", None)
    requested_project = params.get("project_provider_resource_id")
    if requested_project and any(
        owner and str(owner) != str(requested_project) for owner in (volume_project, server_project)
    ):
        raise ValueError("PROJECT_OWNERSHIP_MISMATCH")
    if volume_project and server_project and str(volume_project) != str(server_project):
        raise ValueError("PROJECT_OWNERSHIP_MISMATCH")
    effective_project = discover_effective_scope(connection).get("project_id")
    if effective_project and any(
        owner and str(owner) != str(effective_project) for owner in (volume_project, server_project)
    ):
        raise ValueError("PROJECT_OWNERSHIP_MISMATCH")
    if operation == "attach":
        attachment = compute.create_volume_attachment(server_id, volume_id)
        return attachment, ResourceOperationState.SUCCEEDED
    if operation == "detach":
        try:
            compute.delete_volume_attachment(server_id, volume_id, ignore_missing=True)
        except (os_exc.NotFoundException, os_exc.ResourceNotFound):
            return None, ResourceOperationState.ALREADY_ABSENT
        return None, ResourceOperationState.SUCCEEDED
    raise ValueError(f"unsupported volume attachment operation: {operation}")


def _network_proxy(connection: Any) -> Any:
    proxy = getattr(connection, "network", None)
    if proxy is None:
        raise ValueError("network service is unavailable")
    return proxy


def _network_type(resource_type: str) -> str:
    return {
        "floatingip": "floating_ip",
        "floating-ip": "floating_ip",
        "security": "security_group",
        "security_rule": "security_group_rule",
        "security-group": "security_group",
        "security-group-rule": "security_group_rule",
        "security.group": "security_group",
        "security.group.rule": "security_group_rule",
        "router.interface": "router_interface",
        "router-interface": "router_interface",
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
