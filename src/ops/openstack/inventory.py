"""Provider-safe OpenStack inventory collectors and mappers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any

from keystoneauth1 import exceptions as ks_exc
from openstack import exceptions as os_exc

COLLECTIONS = (
    "region",
    "domain",
    "project",
    "flavor",
    "image",
    "network",
    "subnet",
    "port",
    "router",
    "security_group",
    "security_group_rule",
    "floating_ip",
    "volume",
    "volume-snapshot",
    "instance",
)

_DROP = object()
_SENSITIVE_KEY_PARTS = (
    "password",
    "token",
    "authorization",
    "private_key",
    "user_data",
    "ca_cert",
)


def _value(resource: Any, name: str, default: Any = None) -> Any:
    value = getattr(resource, name, default)
    return default if value is None else value


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None


def _bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    return None


def _volume_attachment_summary(value: Any) -> list[dict[str, Any]]:
    """Keep only bounded, non-secret attachment identity fields."""
    if not isinstance(value, list | tuple):
        return []
    summaries: list[dict[str, Any]] = []
    for attachment in value[:32]:
        if not isinstance(attachment, Mapping):
            continue
        summary = {
            key: str(attachment[key])
            for key in ("server_id", "device", "attachment_id", "volume_id")
            if attachment.get(key) is not None
        }
        if summary:
            summaries.append(summary)
    return summaries


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _sanitize_value(value: Any, *, key: str = "", depth: int = 0) -> Any:
    """Convert nested provider values into bounded JSON-safe primitives."""
    if depth > 8 or _is_sensitive_key(key):
        return _DROP
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, bytes):
        return _DROP
    resource_id = getattr(value, "id", None)
    if resource_id is not None and not isinstance(value, Mapping):
        return {"id": str(resource_id)}
    if isinstance(value, Mapping):
        mapping_result: dict[str, Any] = {}
        for child_key, child_value in value.items():
            normalized_key = str(child_key)
            sanitized = _sanitize_value(child_value, key=normalized_key, depth=depth + 1)
            if sanitized is not _DROP:
                mapping_result[normalized_key] = sanitized
        return mapping_result
    if isinstance(value, list | tuple | set | frozenset):
        result: list[Any] = []
        for child_value in value:
            sanitized = _sanitize_value(child_value, depth=depth + 1)
            if sanitized is not _DROP:
                result.append(sanitized)
        return result
    return _DROP


def map_resource(resource_type: str, resource: Any) -> dict[str, Any]:
    """Map one SDK resource to the common contract without leaking SDK objects."""
    provider_id = str(_value(resource, "id", ""))
    name_value = _value(resource, "name", provider_id)
    name = str(name_value or provider_id)
    item: dict[str, Any] = {
        "provider_resource_id": provider_id,
        "name": name,
        "provider_status": _value(resource, "status"),
        "provider_created_at": _iso(_value(resource, "created_at")),
        "provider_updated_at": _iso(_value(resource, "updated_at")),
        "attributes": {},
    }
    if resource_type == "volume":
        project_id = _value(resource, "project_id") or _value(resource, "tenant_id")
        volume_type = _value(resource, "volume_type")
        if hasattr(volume_type, "id"):
            volume_type = volume_type.id
        for key, value in {
            "project_provider_resource_id": project_id,
            "volume_type_provider_resource_id": volume_type,
            "size_gib": _value(resource, "size"),
            "bootable": _bool(_value(resource, "bootable")),
            "root": _bool(_value(resource, "root"))
            if _value(resource, "root") is not None
            else _bool(_value(resource, "is_root")),
            "encrypted": _bool(_value(resource, "encrypted")),
            "metadata": _value(resource, "metadata"),
            "availability_zone": _value(resource, "availability_zone"),
            "attachments": _volume_attachment_summary(_value(resource, "attachments", [])),
        }.items():
            if value is not None:
                item[key] = value
    if resource_type in {"snapshot", "volume-snapshot"}:
        project_id = _value(resource, "project_id") or _value(resource, "tenant_id")
        if project_id is not None:
            item["project_provider_resource_id"] = project_id
    attributes: dict[str, Any] = {}
    fields = {
        "region": ("description", "parent_region_id"),
        "domain": ("description", "is_enabled"),
        "project": ("domain_id", "domain_name", "description", "is_enabled"),
        "flavor": ("vcpus", "ram", "disk", "ephemeral", "swap", "is_public"),
        "image": ("visibility", "size", "min_disk", "min_ram", "disk_format", "checksum"),
        "network": ("admin_state_up", "shared", "is_router_external", "mtu"),
        "subnet": (
            "network_id",
            "cidr",
            "ip_version",
            "gateway_ip",
            "enable_dhcp",
            "dns_nameservers",
            "allocation_pools",
        ),
        "port": (
            "network_id",
            "admin_state_up",
            "mac_address",
            "fixed_ips",
            "device_id",
            "device_owner",
            "security_group_ids",
        ),
        "router": ("admin_state_up", "external_gateway_info", "status", "distributed"),
        "security_group": ("project_id", "stateful", "description"),
        "security_group_rule": (
            "security_group_id",
            "direction",
            "ethertype",
            "protocol",
            "port_range_min",
            "port_range_max",
            "remote_ip_prefix",
            "remote_group_id",
        ),
        "floating_ip": (
            "floating_network_id",
            "floating_ip_address",
            "port_id",
            "fixed_ip_address",
            "router_id",
            "status",
            "project_id",
        ),
        "volume": (
            "size",
            "volume_type",
            "bootable",
            "encrypted",
            "multiattach",
            "availability_zone",
            "attachments",
        ),
        "snapshot": ("project_id", "volume_id", "size", "description", "metadata"),
        "volume-snapshot": ("project_id", "volume_id", "size", "description", "metadata"),
        "instance": (
            "power_state",
            "flavor",
            "image",
            "OS-EXT-AZ:availability_zone",
            "addresses",
            "metadata",
            "attachments",
            "launched_at",
            "terminated_at",
        ),
    }.get(resource_type, ())
    for field in fields:
        key = field.replace("OS-EXT-AZ:", "").replace(":", "_")
        value = _value(resource, field, None)
        if value is None:
            continue
        if field in {"flavor", "image"} and hasattr(value, "id"):
            value = str(value.id)
        sanitized = _sanitize_value(value, key=key)
        if sanitized is not _DROP:
            attributes[key] = sanitized
    item["attributes"] = attributes
    return item


def collect_resources(connection: Any, resource_type: str) -> list[dict[str, Any]]:
    """Collect one resource type through supported SDK proxy generators."""
    proxy_name = {
        "region": ("identity", "regions"),
        "domain": ("identity", "domains"),
        "project": ("identity", "projects"),
        "flavor": ("compute", "flavors"),
        "image": ("image", "images"),
        "network": ("network", "networks"),
        "subnet": ("network", "subnets"),
        "port": ("network", "ports"),
        "router": ("network", "routers"),
        "security_group": ("network", "security_groups"),
        "security_group_rule": ("network", "security_group_rules"),
        "floating_ip": ("network", "ips"),
        "volume": ("block_storage", "volumes"),
        "snapshot": ("block_storage", "snapshots"),
        "volume-snapshot": ("block_storage", "snapshots"),
        "instance": ("compute", "servers"),
    }
    service, method_name = proxy_name[resource_type]
    proxy = getattr(connection, service)
    resources: Iterable[Any] = getattr(proxy, method_name)()
    mapped = [map_resource(resource_type, resource) for resource in resources]
    # Provider pagination/order is not a contract. Stable ordering keeps
    # redelivery checksums equivalent even when Keystone returns a different
    # page order.
    return sorted(mapped, key=lambda item: item["provider_resource_id"])


def collect_targeted_resource(
    connection: Any, resource_type: str, provider_resource_id: str
) -> dict[str, Any]:
    getter = {
        "region": ("identity", "get_region"),
        "domain": ("identity", "get_domain"),
        "project": ("identity", "get_project"),
        "flavor": ("compute", "get_flavor"),
        "image": ("image", "get_image"),
        "network": ("network", "get_network"),
        "subnet": ("network", "get_subnet"),
        "port": ("network", "get_port"),
        "router": ("network", "get_router"),
        "security_group": ("network", "get_security_group"),
        "security_group_rule": ("network", "get_security_group_rule"),
        "floating_ip": ("network", "get_ip"),
        "volume": ("block_storage", "get_volume"),
        "snapshot": ("block_storage", "get_snapshot"),
        "volume-snapshot": ("block_storage", "get_snapshot"),
        "instance": ("compute", "get_server"),
    }[resource_type]
    proxy = getattr(connection, getter[0])
    method = getattr(proxy, getter[1])
    resource = method(provider_resource_id)
    return map_resource(resource_type, resource)


def collect_instance_relationships(
    connection: Any, instance_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ports: list[dict[str, Any]] = []
    volumes: list[dict[str, Any]] = []
    try:
        if connection.has_service("network"):
            ports = [
                map_resource("port", item)
                for item in connection.network.ports(device_id=instance_id)
            ]
    except (
        AttributeError,
        TypeError,
        ks_exc.EndpointNotFound,
        os_exc.EndpointNotFound,
        os_exc.ServiceDisabledException,
        os_exc.ServiceDiscoveryException,
    ):
        pass
    try:
        if connection.has_service("block-storage"):
            for item in connection.block_storage.volumes():
                if any(
                    attachment.get("server_id") == instance_id
                    for attachment in (item.attachments or [])
                ):
                    volumes.append(map_resource("volume", item))
    except (
        AttributeError,
        TypeError,
        ks_exc.EndpointNotFound,
        os_exc.EndpointNotFound,
        os_exc.ServiceDisabledException,
        os_exc.ServiceDiscoveryException,
    ):
        pass
    return ports, volumes
