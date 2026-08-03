"""Replay-safe identity, assignment, and quota operations.

The handler intentionally uses only OpenStackSDK proxy methods.  CPS owns
durability and idempotency; OPS makes the provider mutation converge to the
requested state and emits a normalized resource-operation result.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import socket
import time
import uuid
from typing import Any

from openstack import exceptions as os_exc

from ops.application.credential_resolver import CpsResolutionError, CredentialResolver
from ops.application.handlers.registry import TypedHandlerFn
from ops.config import Settings
from ops.contracts.errors import CommonError, ErrorCategory
from ops.contracts.messages.delivery import DeliveryMetadata
from ops.contracts.messages.envelope import MessageEnvelope
from ops.contracts.messages.image_operations import ImageOperationRequest
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
from ops.openstack.volume_lifecycle import (
    VOLUME_ATTACH_TARGET_STATUS,
    VOLUME_DETACH_RETRY_ATTEMPTS,
    VOLUME_DETACH_RETRY_INTERVAL_SECONDS,
    VolumeStateConflictError,
    assert_snapshot_force_for_in_use_volume,
    assert_volume_deletable,
    normalize_volume_state_conflict,
    wait_for_volume_detached,
    wait_for_volume_status,
)


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
    if request.resource_type.lower() == "image":
        _validate_image_request(request)
    return request


def _validate_image_request(request: ResourceOperationRequest) -> ImageOperationRequest:
    """Apply the pinned no-bytes/SSRF policy before credentials or SDK access."""
    parameters = dict(request.parameters)
    parameters.pop("operation_marker", None)
    typed = ImageOperationRequest.model_validate(
        {
            "operation_id": request.operation_id,
            "resource_type": "image",
            "operation": request.operation,
            "required_scope": request.required_scope,
            "provider_connection_id": request.provider_connection_id,
            "provider_resource_id": request.provider_resource_id,
            **parameters,
        }
    )
    if typed.source_url:
        _assert_public_import_target(typed.source_url)
    return typed


def _assert_public_import_target(source_url: str) -> None:
    """Reject DNS answers that could direct Glance import at local networks."""
    from urllib.parse import urlsplit

    host = urlsplit(source_url).hostname
    if not host:
        raise ValueError("source URL host is invalid")
    # Local OpenStack integration labs may deliberately expose a self-signed
    # HTTPS fixture on a private address.  Keep the SSRF guard enabled by
    # default; only an explicitly configured, already-allowlisted host may
    # opt into this dev/test exception.
    from ops.contracts.messages.image_operations import IMAGE_IMPORT_ALLOWED_HOSTS

    if (
        os.getenv("OPS_IMAGE_IMPORT_ALLOW_PRIVATE_HOSTS", "false").lower() == "true"
        and host.lower().rstrip(".") in IMAGE_IMPORT_ALLOWED_HOSTS
    ):
        return
    try:
        answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("source URL host could not be resolved") from exc
    addresses = {item[4][0] for item in answers}
    if not addresses:
        raise ValueError("source URL host has no addresses")
    for answer in addresses:
        address = ipaddress.ip_address(answer)
        if not address.is_global:
            raise ValueError("source URL resolves to a non-public address")


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


def _provider_service_for_resource(resource_type: str) -> str:
    """Map CPS resource_type to OpenStack service for error normalization."""
    normalized = resource_type.lower().replace("network.", "").replace("identity.", "")
    if "volume" in normalized or normalized in {"snapshot", "volume-attachment"}:
        return "block_storage"
    if normalized in {
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
        "router_interface",
        "router-interface",
    }:
        return "network"
    if normalized in {"domain", "project", "role", "user", "quota"}:
        return "identity"
    if normalized in {"image", "flavor"}:
        return "image"
    if normalized in {"server", "instance", "keypair"}:
        return "compute"
    return "identity"


def _preflight_network_guardrails(request: ResourceOperationRequest) -> None:
    resource_type = request.resource_type.lower().replace("network.", "")
    kind = _network_type(resource_type)
    operation = request.operation.lower()
    if kind == "floating_ip" and operation == "associate":
        if not request.parameters.get("port_id"):
            raise ValueError("port_id is required")
    if kind in {"network", "subnet", "security_group_rule"}:
        _validate_network_parameters(kind, operation, dict(request.parameters))


def _resource_payload(
    request: ResourceOperationRequest, resource: Any, resource_type: str
) -> dict[str, Any]:
    if resource_type.lower() == "keypair":
        return map_resource(resource_type, resource)
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
        try:
            _preflight_network_guardrails(request)
        except ValueError as exc:
            error = CommonError(
                code="NETWORK_POLICY_VIOLATION",
                message=str(exc),
                category=ErrorCategory.AUTHORIZATION,
                retryable=False,
            )
            return HandlerFailedResult(
                result_routing_key=OPERATION_FAILED,
                result_body=_event(
                    command,
                    error.model_dump(mode="json"),
                    "resource.operation.network-policy",
                    OPERATION_FAILED,
                ),
            )
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
    except VolumeStateConflictError as exc:
        resource_type = str(getattr(command, "payload", {}).get("resource_type", ""))
        error = normalize_volume_state_conflict(
            exc,
            service=_provider_service_for_resource(resource_type),
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
    except Exception as exc:
        resource_type = str(getattr(command, "payload", {}).get("resource_type", ""))
        error = normalize_openstack_exception(
            exc,
            service=_provider_service_for_resource(resource_type),
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
    if resource_type == "keypair":
        return _execute_keypair(connection, operation, provider_id, params)
    if resource_type == "flavor":
        return _execute_flavor(connection, operation, provider_id, params)
    if resource_type == "image":
        _validate_image_request(request)
        return _execute_image(connection, operation, provider_id, params)
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


def _flavor_proxy(connection: Any) -> Any:
    proxy = getattr(connection, "compute", None)
    if proxy is None:
        raise ValueError("compute service is unavailable")
    return proxy


def _flavor_matches(flavor: Any, params: dict[str, Any]) -> bool:
    """Compare the Nova shape CPS treats as immutable after creation."""
    expected = {
        "name": params.get("name"),
        "vcpus": params.get("vcpus"),
        "ram": params.get("ram_mib"),
        "disk": params.get("disk_gib"),
        "ephemeral": params.get("ephemeral_gib", 0),
        "swap": params.get("swap_mib", 0),
        "is_public": params.get("is_public"),
    }
    aliases = {"ram": "ram", "disk": "disk", "is_public": "is_public"}
    for key, value in expected.items():
        if value is None:
            continue
        actual = getattr(flavor, aliases.get(key, key), None)
        if actual is None and key in {"ephemeral", "swap"}:
            actual = 0
        if str(actual) != str(value):
            return False
    return True


def _get_flavor(proxy: Any, provider_id: str) -> Any:
    return proxy.get_flavor(provider_id)


def _execute_flavor(
    connection: Any,
    operation: str,
    provider_id: str | None,
    params: dict[str, Any],
) -> tuple[Any | None, ResourceOperationState]:
    """Converge Nova flavor lifecycle mutations without replace-by-delete."""
    proxy = _flavor_proxy(connection)
    if operation == "create":
        if provider_id:
            try:
                existing = _get_flavor(proxy, provider_id)
            except (os_exc.NotFoundException, os_exc.ResourceNotFound):
                existing = None
            if existing is not None:
                if not _flavor_matches(existing, params):
                    raise os_exc.ConflictException("existing flavor has a different shape")
                return existing, ResourceOperationState.SUCCEEDED
        else:
            existing = next(
                (item for item in proxy.flavors() if getattr(item, "name", None) == params["name"]),
                None,
            )
            if existing is not None:
                if not _flavor_matches(existing, params):
                    raise os_exc.ConflictException("existing flavor has a different shape")
                return existing, ResourceOperationState.SUCCEEDED
        create_params = {
            "name": params["name"],
            "vcpus": params["vcpus"],
            "ram": params["ram_mib"],
            "disk": params["disk_gib"],
            "ephemeral": params.get("ephemeral_gib", 0),
            "swap": params.get("swap_mib", 0),
            "is_public": params.get("is_public", True),
        }
        if provider_id:
            create_params["flavorid"] = provider_id
        return proxy.create_flavor(**create_params), ResourceOperationState.SUCCEEDED
    if not provider_id:
        raise ValueError("provider_resource_id is required")
    try:
        flavor = _get_flavor(proxy, provider_id)
    except (os_exc.NotFoundException, os_exc.ResourceNotFound):
        if operation == "delete":
            return None, ResourceOperationState.ALREADY_ABSENT
        raise
    if operation == "delete":
        proxy.delete_flavor(flavor, ignore_missing=True)
        return None, ResourceOperationState.SUCCEEDED
    if operation == "replace_access":
        desired = {str(item) for item in params.get("access_project_ids", [])}
        current = {
            str(getattr(item, "tenant_id", getattr(item, "project_id", "")))
            for item in proxy.get_flavor_access(flavor)
        }
        for project_id in sorted(desired - current):
            proxy.flavor_add_tenant_access(flavor, project_id)
        for project_id in sorted(current - desired):
            proxy.flavor_remove_tenant_access(flavor, project_id)
        return flavor, ResourceOperationState.SUCCEEDED
    if operation == "patch_extra_specs":
        updates = dict(params.get("extra_specs", {}))
        removals = set(params.get("remove_extra_spec_keys", []))
        fetched = proxy.fetch_flavor_extra_specs(flavor)
        current_specs = dict(
            getattr(fetched, "extra_specs", fetched if isinstance(fetched, dict) else {}) or {}
        )
        for key in sorted(removals):
            if key in current_specs:
                proxy.delete_flavor_extra_specs_property(flavor, key)
        if updates:
            proxy.create_flavor_extra_specs(flavor, extra_specs=updates)
        return flavor, ResourceOperationState.SUCCEEDED
    raise ValueError(f"unsupported flavor operation: {operation}")


def _image_proxy(connection: Any) -> Any:
    proxy = getattr(connection, "image", None)
    if proxy is None:
        raise ValueError("image service is unavailable")
    return proxy


def _get_image(proxy: Any, provider_id: str) -> Any:
    return proxy.get_image(provider_id)


def _find_image_by_marker(proxy: Any, marker: str | None) -> Any | None:
    if not marker or not callable(getattr(proxy, "images", None)):
        return None
    for image in proxy.images():
        properties = getattr(image, "properties", {}) or {}
        if properties.get("cmp_operation_marker") == marker or (
            getattr(image, "cmp_operation_marker", None) == marker
        ):
            return image
    return None


def _execute_image(
    connection: Any,
    operation: str,
    provider_id: str | None,
    params: dict[str, Any],
) -> tuple[Any | None, ResourceOperationState]:
    """Converge supported Glance operations without fetching image content."""
    proxy = _image_proxy(connection)
    if operation in {"create", "import_url"}:
        image = _find_image_by_marker(proxy, params.get("operation_marker"))
        if image is None:
            image_fields = {
                "name": params.get("name"),
                "description": params.get("description"),
                "disk_format": params.get("disk_format"),
                "container_format": params.get("container_format", "bare"),
                "visibility": params.get("visibility"),
                "protected": params.get("protected"),
                "tags": params.get("tags") or None,
                "min_disk": params.get("min_disk_gib", 0),
                "min_ram": params.get("min_ram_mib", 0),
                "architecture": params.get("architecture"),
                "kernel_id": params.get("kernel_id"),
                "ramdisk_id": params.get("ramdisk_id"),
            }
            create = {key: value for key, value in image_fields.items() if value is not None}
            marker = params.get("operation_marker")
            if marker:
                create["properties"] = {"cmp_operation_marker": marker}
            image = proxy.create_image(**create)
        if operation == "import_url":
            # The Pydantic contract was revalidated above: this is a provider-side
            # reference, never a CPS/OPS HTTP fetch or byte payload.
            if getattr(image, "status", "").lower() not in {"active", "importing"}:
                proxy.import_image(image, method="web-download", uri=params["source_url"])
        return image, ResourceOperationState.SUCCEEDED
    if not provider_id:
        raise ValueError("image lifecycle operation requires provider_resource_id")
    try:
        image = _get_image(proxy, provider_id)
    except (os_exc.NotFoundException, os_exc.ResourceNotFound, KeyError):
        if operation == "delete":
            return None, ResourceOperationState.ALREADY_ABSENT
        raise
    if operation == "delete":
        if bool(getattr(image, "protected", False)):
            raise os_exc.ConflictException("protected image cannot be deleted")
        proxy.delete_image(image, ignore_missing=True)
        return None, ResourceOperationState.SUCCEEDED
    if operation == "patch_metadata":
        metadata = dict(params.get("metadata", {}))
        removals = list(params.get("remove_metadata_keys", []))
        updater = getattr(proxy, "update_image_properties", None)
        if removals and callable(updater):
            updater(image, remove=removals)
        if metadata:
            proxy.update_image(image, **metadata)
        return image, ResourceOperationState.SUCCEEDED
    if operation == "set_visibility":
        return (
            proxy.update_image(image, visibility=params.get("visibility")),
            ResourceOperationState.SUCCEEDED,
        )
    if operation == "set_protection":
        return (
            proxy.update_image(image, protected=params.get("protected")),
            ResourceOperationState.SUCCEEDED,
        )
    if operation == "grant_member":
        proxy.add_member(image, params["member_project_id"])
        return image, ResourceOperationState.SUCCEEDED
    if operation == "revoke_member":
        proxy.remove_member(image, params["member_project_id"])
        return image, ResourceOperationState.SUCCEEDED
    if operation == "deactivate":
        proxy.deactivate_image(image)
        return image, ResourceOperationState.SUCCEEDED
    if operation == "reactivate":
        proxy.reactivate_image(image)
        return image, ResourceOperationState.SUCCEEDED
    raise ValueError(f"unsupported image operation: {operation}")


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
        proxy.extend_volume(existing, requested_size)
        refreshed = _get_volume(proxy, provider_id)
        waiter = getattr(proxy, "wait_for_status", None)
        if callable(waiter):
            refreshed = waiter(
                refreshed,
                status="available",
                failures=sorted({"error", "error_extending"}),
                interval=1,
                wait=300,
            )
        return refreshed, ResourceOperationState.SUCCEEDED

    if operation == "delete":
        assert_volume_deletable(existing)
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
        assert_snapshot_force_for_in_use_volume(volume, force=bool(params.get("force", False)))
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


def _validate_public_key(public_key: Any) -> str:
    if not isinstance(public_key, str) or not 32 <= len(public_key) <= 16384:
        raise ValueError("public key must be between 32 and 16384 characters")
    lowered = public_key.lower()
    if "private key" in lowered or "begin openssh private key" in lowered:
        raise ValueError("PRIVATE_KEY_MATERIAL_REJECTED")
    if not lowered.startswith(("ssh-rsa ", "ssh-ed25519 ", "ecdsa-sha2-", "sk-ssh-")):
        raise ValueError("unsupported public key format")
    return public_key


def _execute_keypair(
    connection: Any,
    operation: str,
    provider_id: str | None,
    params: dict[str, Any],
) -> tuple[Any | None, ResourceOperationState]:
    """Manage Nova keypairs using public material only."""
    compute = getattr(connection, "compute", None)
    if compute is None:
        raise ValueError("compute service is unavailable")
    name = params.get("name")
    if operation in {"create", "import", "ensure"}:
        if not name:
            raise ValueError("keypair create requires name")
        requested_key = _validate_public_key(params.get("public_key"))
        existing = compute.find_keypair(name, ignore_missing=True)
        if existing is not None:
            _keypair_owner_check(connection, params, existing)
            if operation == "ensure":
                return existing, ResourceOperationState.SUCCEEDED
            existing_key = getattr(existing, "public_key", None)
            if existing_key and str(existing_key).strip() == requested_key.strip():
                return existing, ResourceOperationState.SUCCEEDED
            raise ValueError("KEYPAIR_NAME_CONFLICT")
        created = compute.create_keypair(name=name, public_key=requested_key)
        _keypair_owner_check(connection, params, created)
        return created, ResourceOperationState.SUCCEEDED
    if not provider_id:
        raise ValueError(f"keypair {operation} requires provider_resource_id")
    try:
        existing = compute.get_keypair(provider_id)
    except (os_exc.NotFoundException, os_exc.ResourceNotFound):
        if operation == "delete":
            return None, ResourceOperationState.ALREADY_ABSENT
        raise
    _keypair_owner_check(connection, params, existing)
    if operation == "delete":
        compute.delete_keypair(existing, ignore_missing=True)
        return None, ResourceOperationState.SUCCEEDED
    raise ValueError(f"unsupported keypair operation: {operation}")


def _keypair_owner_check(connection: Any, params: dict[str, Any], resource: Any) -> None:
    effective_project = discover_effective_scope(connection).get("project_id")
    requested_project = params.get("project_id") or params.get("project_provider_resource_id")
    resource_project = getattr(resource, "project_id", None) or getattr(resource, "tenant_id", None)
    if effective_project and requested_project and str(effective_project) != str(requested_project):
        raise ValueError("PROJECT_OWNERSHIP_MISMATCH")
    if effective_project and resource_project and str(effective_project) != str(resource_project):
        raise ValueError("PROJECT_OWNERSHIP_MISMATCH")
    if requested_project and resource_project and str(requested_project) != str(resource_project):
        raise ValueError("PROJECT_OWNERSHIP_MISMATCH")


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
        volume = wait_for_volume_status(
            block_storage,
            block_storage.get_volume(volume_id),
            target_status=VOLUME_ATTACH_TARGET_STATUS,
        )
        device = getattr(attachment, "device", None)
        if device is None:
            for item in getattr(volume, "attachments", None) or []:
                attached_server = getattr(item, "server_id", None) or getattr(
                    item, "instance_id", None
                )
                if attached_server is not None and str(attached_server) == server_id:
                    device = getattr(item, "device", None)
                    break
        if device is not None and getattr(attachment, "device", None) is None:
            attachment.device = device
        return attachment, ResourceOperationState.SUCCEEDED
    if operation == "detach":
        try:
            for attempt in range(VOLUME_DETACH_RETRY_ATTEMPTS):
                try:
                    compute.delete_volume_attachment(server_id, volume_id, ignore_missing=True)
                    break
                except os_exc.ConflictException:
                    if attempt == VOLUME_DETACH_RETRY_ATTEMPTS - 1:
                        raise
                    time.sleep(VOLUME_DETACH_RETRY_INTERVAL_SECONDS)
        except (os_exc.NotFoundException, os_exc.ResourceNotFound):
            volume = wait_for_volume_detached(
                block_storage,
                server_id=server_id,
                volume_id=volume_id,
            )
            return volume, ResourceOperationState.ALREADY_ABSENT
        volume = wait_for_volume_detached(
            block_storage,
            server_id=server_id,
            volume_id=volume_id,
        )
        return volume, ResourceOperationState.SUCCEEDED
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


def _get_floating_ip(proxy: Any, provider_id: str) -> Any:
    """Resolve a floating IP by exact provider ID.

    Some project-scoped Neutron endpoints reject ``get_ip`` while still
    listing the same ID via ``ips()``. Fall back to an exact-ID list scan only
    after ``get_ip`` raises ``NotFoundException``.
    """
    try:
        return proxy.get_ip(provider_id)
    except os_exc.NotFoundException:
        for item in proxy.ips():
            if getattr(item, "id", None) == provider_id:
                return item
        raise


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
        _network_owner_check(connection, params, router)
        if relation.get("subnet_id"):
            _network_owner_check(connection, params, proxy.get_subnet(str(relation["subnet_id"])))
        if relation.get("port_id"):
            _network_owner_check(connection, params, proxy.get_port(str(relation["port_id"])))
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
            external_network = proxy.get_network(str(network_id))
            if not bool(
                getattr(external_network, "is_router_external", False)
                or getattr(external_network, "router_external", False)
            ):
                raise ValueError("floating IP network must be external")
            _network_quota_check(connection, proxy, "floating_ip", params)
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
            existing = _get_floating_ip(proxy, provider_id)
        except os_exc.NotFoundException:
            return (
                (None, ResourceOperationState.ALREADY_ABSENT)
                if operation in {"release", "delete"}
                else (_raise("floating IP not found"))
            )
        if operation in {"associate", "disassociate", "update", "patch"}:
            if operation == "associate" and not params.get("port_id"):
                raise ValueError("port_id is required")
            if operation == "associate":
                port = proxy.get_port(str(params["port_id"]))
                _network_owner_check(connection, params, port)
            else:
                _network_owner_check(connection, params, existing)
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
        if kind in {"subnet", "port"}:
            _network_owner_check(connection, params, proxy.get_network(str(params["network_id"])))
        if kind == "security_group_rule":
            _network_owner_check(
                connection,
                params,
                proxy.get_security_group(str(params["security_group_id"])),
            )
        _network_quota_check(connection, proxy, kind, params)
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
    if kind == "network" and any(
        params.get(key) is True for key in ("external", "is_router_external", "router:external")
    ):
        raise ValueError("external network mutation is administrator-only")
    if kind == "subnet":
        try:
            requested = ipaddress.ip_network(str(params["cidr"]), strict=False)
        except ValueError as exc:
            raise ValueError("invalid subnet cidr") from exc
        gateway = params.get("gateway_ip")
        if gateway is not None and ipaddress.ip_address(str(gateway)) not in requested:
            raise ValueError("gateway_ip must be inside subnet cidr")
        for pool in params.get("allocation_pools", []) or []:
            if not isinstance(pool, dict) or not pool.get("start") or not pool.get("end"):
                raise ValueError("invalid allocation pool")
            start = ipaddress.ip_address(str(pool["start"]))
            end = ipaddress.ip_address(str(pool["end"]))
            if (
                start.version != end.version
                or start not in requested
                or end not in requested
                or int(start) > int(end)
            ):
                raise ValueError("allocation pool must be inside subnet cidr")
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
        remote_prefix = params.get("remote_ip_prefix")
        if remote_prefix is not None:
            try:
                remote_network = ipaddress.ip_network(str(remote_prefix), strict=False)
            except ValueError as exc:
                raise ValueError("remote_ip_prefix must be a valid network") from exc
            if direction == "ingress" and remote_network.prefixlen == 0:
                raise ValueError("public ingress rules require administrator policy")


def _network_quota_check(
    connection: Any,
    proxy: Any,
    kind: str,
    params: dict[str, Any],
) -> None:
    """Recheck Neutron limits immediately before a create mutation."""
    get_quota = getattr(proxy, "get_quota", None)
    plural = {
        "network": "networks",
        "subnet": "subnets",
        "router": "routers",
        "port": "ports",
        "security_group": "security_groups",
        "security_group_rule": "security_group_rules",
        "floating_ip": "floating_ips",
    }.get(kind)
    if not callable(get_quota) or plural is None:
        return
    project_id = params.get("project_id") or discover_effective_scope(connection).get("project_id")
    if not project_id:
        raise ValueError("PROJECT_OWNERSHIP_MISMATCH")
    quota = get_quota(str(project_id))
    limit = getattr(quota, plural, None)
    if not isinstance(limit, int) or limit < 0:
        return
    lister = getattr(proxy, {"floating_ips": "ips"}.get(plural, plural), None)
    if not callable(lister):
        return
    try:
        used = sum(1 for _ in lister(project_id=str(project_id)))
    except TypeError:
        used = sum(
            1 for item in lister() if str(getattr(item, "project_id", "")) == str(project_id)
        )
    if used >= limit:
        raise ValueError("NETWORK_QUOTA_EXCEEDED")


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
