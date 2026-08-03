"""Provider-safe OpenStack inventory collectors and mappers."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any

from keystoneauth1 import exceptions as ks_exc
from openstack import exceptions as os_exc

from ops.openstack.scope import discover_effective_scope

COLLECTIONS = (
    "region",
    "domain",
    "project",
    "flavor",
    "availability-zone",
    "image",
    "network",
    "subnet",
    "port",
    "router",
    "security_group",
    "security_group_rule",
    "floating_ip",
    "volume",
    "volume-type",
    "volume-snapshot",
    "keypair",
    "instance",
)

_COLLECTION_HYPHEN_ALIASES = {
    "security-group": "security_group",
    "security-group-rule": "security_group_rule",
    "floating-ip": "floating_ip",
}


def normalize_collection_name(name: str) -> str | None:
    """Map CPS catalog collection identifiers to OPS collector names."""
    if name in COLLECTIONS:
        return name
    aliased = _COLLECTION_HYPHEN_ALIASES.get(name)
    if aliased is not None:
        return aliased
    underscored = name.replace("-", "_")
    if underscored in COLLECTIONS:
        return underscored
    return None


def normalize_collection_names(names: list[str]) -> list[str]:
    """Drop unsupported collections and dedupe while preserving order."""
    normalized: list[str] = []
    seen: set[str] = set()
    for name in names:
        if not isinstance(name, str):
            continue
        mapped = normalize_collection_name(name)
        if mapped is None or mapped in seen:
            continue
        seen.add(mapped)
        normalized.append(mapped)
    return normalized


_DROP = object()
_SENSITIVE_KEY_PARTS = (
    "password",
    "token",
    "authorization",
    "private_key",
    "user_data",
    "ca_cert",
)
_MAX_CATALOG_ACCESS_LOOKUPS = 256
_MAX_CATALOG_METADATA_BYTES = 64 * 1024
_MAX_CATALOG_METADATA_DEPTH = 4
_MAX_CATALOG_METADATA_ENTRIES = 128
_MAX_CATALOG_METADATA_STRING_LENGTH = 4096


def _value(resource: Any, name: str, default: Any = None) -> Any:
    if isinstance(resource, Mapping):
        return resource.get(name, default)
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


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _sanitize_catalog_metadata(value: Any, *, key: str = "", depth: int = 0) -> Any:
    """Match CPS catalog metadata bounds while dropping provider secrets."""
    if depth > _MAX_CATALOG_METADATA_DEPTH or _is_sensitive_key(key):
        return _DROP
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value if len(value) <= _MAX_CATALOG_METADATA_STRING_LENGTH else _DROP
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for child_key, child_value in sorted(value.items(), key=lambda item: str(item[0])):
            normalized_key = str(child_key)
            if not normalized_key or len(normalized_key) > 255 or _is_sensitive_key(normalized_key):
                continue
            sanitized = _sanitize_catalog_metadata(child_value, key=normalized_key, depth=depth + 1)
            if sanitized is not _DROP:
                result[normalized_key] = sanitized
            if len(result) >= _MAX_CATALOG_METADATA_ENTRIES:
                break
        return result
    if isinstance(value, list | tuple | set | frozenset):
        list_result: list[Any] = []
        for child_value in value:
            sanitized = _sanitize_catalog_metadata(child_value, depth=depth + 1)
            if sanitized is not _DROP:
                list_result.append(sanitized)
            if len(list_result) >= _MAX_CATALOG_METADATA_ENTRIES:
                break
        return list_result
    return _DROP


def _safe_catalog_mapping(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    sanitized = _sanitize_catalog_metadata(value)
    if not isinstance(sanitized, dict):
        return None
    while (
        sanitized
        and len(
            json.dumps(sanitized, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
                "utf-8"
            )
        )
        > _MAX_CATALOG_METADATA_BYTES
    ):
        sanitized.pop(next(reversed(sanitized)))
    return sanitized


def _project_access_ids(value: Any) -> list[str] | None:
    if not isinstance(value, list | tuple | set | frozenset):
        return None
    project_ids = {str(item) for item in value if isinstance(item, str | int) and str(item)}
    return sorted(project_ids)


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
    if resource_type == "keypair":
        project_id = _value(resource, "project_id") or _value(resource, "tenant_id")
        if project_id is not None:
            item["project_provider_resource_id"] = project_id
    if resource_type == "image":
        project_id = _value(resource, "owner") or _value(resource, "owner_id")
        catalog_fields = {
            "project_provider_resource_id": project_id,
            "visibility": _value(resource, "visibility"),
            "is_protected": _bool(_value(resource, "protected")),
            "container_format": _value(resource, "container_format"),
            "disk_format": _value(resource, "disk_format"),
            "size_bytes": _integer(_value(resource, "size")),
            "virtual_size_bytes": _integer(_value(resource, "virtual_size")),
            "properties": _safe_catalog_mapping(_value(resource, "properties")),
            "checksum": _value(resource, "checksum"),
            "min_disk_gib": _integer(_value(resource, "min_disk")),
            "min_ram_mib": _integer(_value(resource, "min_ram")),
        }
        for key, value in catalog_fields.items():
            if value is not None:
                item[key] = value
    if resource_type == "flavor":
        disabled = _bool(_value(resource, "disabled"))
        catalog_fields = {
            "vcpus": _integer(_value(resource, "vcpus")),
            "ram_mib": _integer(_value(resource, "ram")),
            "root_disk_gib": _integer(_value(resource, "disk")),
            "ephemeral_disk_gib": _integer(_value(resource, "ephemeral")),
            "swap_mib": _integer(_value(resource, "swap")),
            "is_public": _bool(_value(resource, "is_public")),
            "enabled": None if disabled is None else not disabled,
            "extra_specs": _safe_catalog_mapping(_value(resource, "extra_specs")),
            "access_project_ids": _project_access_ids(_value(resource, "access_project_ids")),
        }
        for key, value in catalog_fields.items():
            if value is not None:
                item[key] = value
    attributes: dict[str, Any] = {}
    fields = {
        "region": ("description", "parent_region_id"),
        "domain": ("description", "is_enabled"),
        "project": ("domain_id", "domain_name", "description", "is_enabled"),
        "flavor": (),
        "availability-zone": ("available",),
        "image": (),
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
        "volume-type": ("is_public", "description", "extra_specs"),
        "snapshot": ("project_id", "volume_id", "size", "description", "metadata"),
        "volume-snapshot": ("project_id", "volume_id", "size", "description", "metadata"),
        "keypair": ("project_id", "fingerprint", "type", "public_key"),
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
    tags = _value(resource, "tags")
    if isinstance(tags, list | tuple | set | frozenset):
        normalized_tags = [str(tag) for tag in tags if tag is not None]
        if resource_type == "image":
            item["tags"] = normalized_tags[:64]
        else:
            attributes["tags"] = normalized_tags[:64]
        catalog_approved = "cmp-catalog-approved=true" in {tag.lower() for tag in normalized_tags}
        if resource_type in {"image", "flavor"}:
            item["catalog_approved"] = catalog_approved
        else:
            attributes["catalog_approved"] = catalog_approved
    metadata = _value(resource, "metadata") or _value(resource, "properties")
    if isinstance(metadata, Mapping):
        approval = metadata.get("cmp-catalog-approved")
        if isinstance(approval, str):
            value = approval.lower() in {"true", "1", "yes"}
            if resource_type in {"image", "flavor"}:
                item["catalog_approved"] = value
            else:
                attributes["catalog_approved"] = value
    extra_specs = _value(resource, "extra_specs")
    if isinstance(extra_specs, Mapping):
        approval = extra_specs.get("cmp-catalog-approved")
        if isinstance(approval, str):
            value = approval.lower() in {"true", "1", "yes"}
            if resource_type == "flavor":
                item["catalog_approved"] = value
            else:
                attributes["catalog_approved"] = value
        elif isinstance(approval, bool):
            if resource_type == "flavor":
                item["catalog_approved"] = approval
            else:
                attributes["catalog_approved"] = approval
    item["attributes"] = attributes
    return item


def collect_resources(connection: Any, resource_type: str) -> list[dict[str, Any]]:
    """Collect one resource type through supported SDK proxy generators."""
    proxy_name = {
        "region": ("identity", "regions"),
        "domain": ("identity", "domains"),
        "project": ("identity", "projects"),
        "flavor": ("compute", "flavors"),
        "availability-zone": ("compute", "availability_zones"),
        "image": ("image", "images"),
        "network": ("network", "networks"),
        "subnet": ("network", "subnets"),
        "port": ("network", "ports"),
        "router": ("network", "routers"),
        "security_group": ("network", "security_groups"),
        "security_group_rule": ("network", "security_group_rules"),
        "floating_ip": ("network", "ips"),
        "volume": ("block_storage", "volumes"),
        "volume-type": ("block_storage", "types"),
        "snapshot": ("block_storage", "snapshots"),
        "volume-snapshot": ("block_storage", "snapshots"),
        "keypair": ("compute", "keypairs"),
        "instance": ("compute", "servers"),
    }
    service, method_name = proxy_name[resource_type]
    proxy = getattr(connection, service)
    if resource_type == "flavor":
        try:
            resources: Iterable[Any] = getattr(proxy, method_name)(get_extra_specs=True)
        except TypeError:
            resources = getattr(proxy, method_name)()
    else:
        resources = getattr(proxy, method_name)()
    if resource_type == "availability-zone":
        approved_zones = {
            str(_value(aggregate, "availability_zone"))
            for aggregate in connection.compute.aggregates()
            if _bool(
                (_value(aggregate, "metadata", {}) or {}).get("cmp-catalog-approved")
                if isinstance(_value(aggregate, "metadata", {}), Mapping)
                else None
            )
            is True
        }
        mapped = []
        for resource in resources:
            zone_name = str(_value(resource, "name") or _value(resource, "zone_name") or "")
            zone_state = _value(resource, "zone_state", {})
            available = (
                zone_state.get("available")
                if isinstance(zone_state, Mapping)
                else _value(resource, "available")
            )
            mapped.append(
                map_resource(
                    resource_type,
                    {
                        "id": zone_name,
                        "name": zone_name,
                        "available": available,
                        "extra_specs": {
                            "cmp-catalog-approved": zone_name in approved_zones,
                        },
                    },
                )
            )
    else:
        mapped = [map_resource(resource_type, resource) for resource in resources]
    if resource_type == "image":
        member_reader = getattr(connection.image, "members", None)
        shared_images = [item for item in mapped if item.get("visibility") == "shared"]
        if len(shared_images) > _MAX_CATALOG_ACCESS_LOOKUPS:
            raise RuntimeError("image member enrichment exceeds bounded lookup budget")
        if callable(member_reader):
            for item in shared_images:
                project_ids = _project_access_ids(
                    [
                        _value(entry, "member_id") or _value(entry, "member")
                        for entry in member_reader(item["provider_resource_id"])
                    ]
                )
                if project_ids is not None:
                    item["access_project_ids"] = project_ids
    if resource_type == "flavor":
        access_reader = getattr(connection.compute, "get_flavor_access", None)
        private_flavors = [item for item in mapped if item.get("is_public") is False]
        if len(private_flavors) > _MAX_CATALOG_ACCESS_LOOKUPS:
            raise RuntimeError("flavor access enrichment exceeds bounded lookup budget")
        if callable(access_reader):
            for item in private_flavors:
                access = access_reader(item["provider_resource_id"])
                project_ids = _project_access_ids(
                    [_value(entry, "tenant_id") or _value(entry, "project_id") for entry in access]
                )
                if project_ids is not None:
                    item["access_project_ids"] = project_ids
    if resource_type == "keypair":
        project_id = discover_effective_scope(connection).get("project_id")
        if project_id:
            for item in mapped:
                item.setdefault("project_provider_resource_id", project_id)
    # Provider pagination/order is not a contract. Stable ordering keeps
    # redelivery checksums equivalent even when Keystone returns a different
    # page order.
    return sorted(mapped, key=lambda item: item["provider_resource_id"])


def collect_targeted_resource(
    connection: Any, resource_type: str, provider_resource_id: str
) -> dict[str, Any]:
    if resource_type == "availability-zone":
        for item in collect_resources(connection, resource_type):
            if item["provider_resource_id"] == provider_resource_id:
                return item
        raise os_exc.ResourceNotFound(message="availability zone not found")
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
        "volume-type": ("block_storage", "get_type"),
        "snapshot": ("block_storage", "get_snapshot"),
        "volume-snapshot": ("block_storage", "get_snapshot"),
        "keypair": ("compute", "get_keypair"),
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
