"""Provider-safe OpenStack inventory collectors and mappers."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from keystoneauth1 import exceptions as ks_exc
from openstack import exceptions as os_exc

from ops.contracts.safe_metadata import validate_safe_project_id
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
    "secret",
    "credential",
)
_IMAGE_EXCLUDED_PROPERTIES = frozenset({"file", "locations", "direct_url", "url", "data"})


class CatalogEnrichmentBudgetExceeded(RuntimeError):
    """Catalog enrichment exceeded its configured provider-call budget."""


class TargetedResourceNotFound(RuntimeError):
    """The targeted base provider resource was confirmed absent."""


class _EnrichmentBudget:
    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.used = 0

    def consume(self) -> None:
        if self.used >= self.maximum:
            raise CatalogEnrichmentBudgetExceeded("catalog enrichment call budget exceeded")
        self.used += 1


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


def _non_negative_int(value: Any, *, empty_zero: bool = False) -> int | None:
    if value == "" and empty_zero:
        return 0
    if isinstance(value, bool):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized >= 0 else None


def _strict_approval(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.lower() == "true")


def _safe_project_ids(values: Any, *, maximum: int = 256) -> list[str]:
    if not isinstance(values, list | tuple | set | frozenset):
        return []
    normalized: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value:
            continue
        try:
            normalized.add(validate_safe_project_id(value, label="project id"))
        except ValueError:
            continue
    return sorted(normalized)[:maximum]


def _safe_map(value: Any, *, excluded: frozenset[str] = frozenset()) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    for raw_key, raw_value in sorted(value.items(), key=lambda pair: str(pair[0])):
        key = str(raw_key)
        if key.lower() in excluded or _is_sensitive_key(key):
            continue
        sanitized = _sanitize_value(raw_value, key=key, depth=1)
        if sanitized is not _DROP:
            result[key] = sanitized
        if len(result) == 128:
            break
    return result


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
    if depth > 4 or _is_sensitive_key(key):
        return _DROP
    if isinstance(value, str):
        parsed = urlsplit(value)
        if parsed.scheme in {"http", "https"}:
            if parsed.username is not None or parsed.password is not None:
                return _DROP
            if any(
                _is_sensitive_key(query_key) or "signature" in query_key.lower()
                for query_key, _ in parse_qsl(parsed.query, keep_blank_values=True)
            ):
                return _DROP
        return value
    if value is None or isinstance(value, int | float | bool):
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


def map_resource(
    resource_type: str,
    resource: Any,
    *,
    access_project_ids: list[Any] | None = None,
    member_project_ids: list[Any] | None = None,
) -> dict[str, Any]:
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
        status = _value(resource, "status")
        if isinstance(status, str):
            item["provider_status"] = status.lower()
        owner = _value(resource, "owner") or _value(resource, "owner_id")
        if owner is not None:
            item["project_provider_resource_id"] = str(owner)
        for key, value in {
            "visibility": str(_value(resource, "visibility", "")).lower() or None,
            "size_bytes": _non_negative_int(_value(resource, "size")),
            "min_disk_gib": _non_negative_int(_value(resource, "min_disk")),
            "min_ram_mib": _non_negative_int(_value(resource, "min_ram")),
            "disk_format": str(_value(resource, "disk_format", "")).lower() or None,
            "checksum": _value(resource, "checksum"),
        }.items():
            if value is not None:
                item[key] = value
    if resource_type == "flavor":
        for key, value in {
            "vcpus": _non_negative_int(_value(resource, "vcpus")),
            "ram_mib": _non_negative_int(_value(resource, "ram")),
            "root_disk_gib": _non_negative_int(_value(resource, "disk")),
            "ephemeral_disk_gib": _non_negative_int(_value(resource, "ephemeral")),
            "swap_mib": _non_negative_int(_value(resource, "swap"), empty_zero=True),
            "is_public": _bool(_value(resource, "is_public")),
        }.items():
            if value is not None:
                item[key] = value
        disabled = _bool(_value(resource, "is_disabled"))
        if disabled is not None:
            item["enabled"] = not disabled
    attributes: dict[str, Any] = {}
    fields = {
        "region": ("description", "parent_region_id"),
        "domain": ("description", "is_enabled"),
        "project": ("domain_id", "domain_name", "description", "is_enabled"),
        "flavor": ("vcpus", "ram", "disk", "ephemeral", "swap", "is_public"),
        "availability-zone": ("available",),
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
        attributes["tags"] = normalized_tags[:64]
        attributes["catalog_approved"] = "cmp-catalog-approved=true" in {
            tag.lower() for tag in normalized_tags
        }
    metadata = _value(resource, "metadata") or _value(resource, "properties")
    if isinstance(metadata, Mapping):
        approval = metadata.get("cmp-catalog-approved")
        if isinstance(approval, str):
            attributes["catalog_approved"] = approval.lower() in {"true", "1", "yes"}
    extra_specs = _value(resource, "extra_specs")
    if isinstance(extra_specs, Mapping):
        approval = extra_specs.get("cmp-catalog-approved")
        if isinstance(approval, str):
            attributes["catalog_approved"] = approval.lower() in {"true", "1", "yes"}
        elif isinstance(approval, bool):
            attributes["catalog_approved"] = approval
    if resource_type == "image":
        protected = _bool(_value(resource, "is_protected", _value(resource, "protected")))
        attributes = {
            "catalog_approved": _strict_approval(
                (_value(resource, "properties") or {}).get("cmp-catalog-approved")
                if isinstance(_value(resource, "properties"), Mapping)
                else None
            )
            or "cmp-catalog-approved=true"
            in {str(tag).lower() for tag in (_value(resource, "tags") or [])},
        }
        if protected is not None:
            attributes["is_protected"] = protected
        container_format = str(_value(resource, "container_format", "")).lower()
        if container_format:
            attributes["container_format"] = container_format
        virtual_size = _non_negative_int(_value(resource, "virtual_size"))
        if virtual_size is not None:
            attributes["virtual_size_bytes"] = virtual_size
        tags = sorted({str(tag)[:255] for tag in (_value(resource, "tags") or [])})[:64]
        if tags:
            attributes["tags"] = tags
        properties = _safe_map(_value(resource, "properties"), excluded=_IMAGE_EXCLUDED_PROPERTIES)
        properties.pop("cmp-catalog-approved", None)
        if properties:
            attributes["properties"] = properties
        normalized_members = _safe_project_ids(member_project_ids)
        if normalized_members:
            attributes["member_project_ids"] = normalized_members
    elif resource_type == "flavor":
        safe_specs = _safe_map(extra_specs)
        approval = safe_specs.pop("cmp-catalog-approved", None)
        attributes = {"catalog_approved": _strict_approval(approval)}
        if safe_specs:
            attributes["extra_specs"] = safe_specs
        access_ids = (
            access_project_ids
            if access_project_ids is not None
            else _value(resource, "access_project_ids", [])
        )
        if access_ids:
            attributes["access_project_ids"] = _safe_project_ids(access_ids)
    item["attributes"] = attributes
    return item


def collect_resources(
    connection: Any, resource_type: str, *, enrichment_max_calls: int = 256
) -> list[dict[str, Any]]:
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
        resources = getattr(proxy, method_name)(details=True, get_extra_specs=False)
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
    elif resource_type == "image":
        budget = _EnrichmentBudget(enrichment_max_calls)
        mapped = []
        for resource in resources:
            member_ids: list[Any] = []
            if str(_value(resource, "visibility", "")).lower() == "shared":
                budget.consume()
                member_ids = [
                    _value(row, "member_id") for row in connection.image.members(resource)
                ]
            mapped.append(
                map_resource(
                    resource_type,
                    resource,
                    member_project_ids=[value for value in member_ids if value],
                )
            )
    elif resource_type == "flavor":
        budget = _EnrichmentBudget(enrichment_max_calls)
        mapped = []
        for resource in resources:
            if not isinstance(_value(resource, "extra_specs"), Mapping):
                budget.consume()
                fetched = connection.compute.fetch_flavor_extra_specs(resource)
                resource = fetched or resource
            access_ids: list[Any] = []
            if _bool(_value(resource, "is_public")) is False:
                budget.consume()
                access = connection.compute.get_flavor_access(resource)
                access_ids = [
                    _value(row, "tenant_id") or _value(row, "project_id") for row in access
                ]
            mapped.append(
                map_resource(
                    resource_type,
                    resource,
                    access_project_ids=[value for value in access_ids if value],
                )
            )
    else:
        mapped = [map_resource(resource_type, resource) for resource in resources]
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
    connection: Any,
    resource_type: str,
    provider_resource_id: str,
    *,
    enrichment_max_calls: int = 256,
) -> dict[str, Any]:
    if resource_type == "availability-zone":
        for item in collect_resources(connection, resource_type):
            if item["provider_resource_id"] == provider_resource_id:
                return item
        raise TargetedResourceNotFound("targeted resource not found")
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
    try:
        if resource_type == "flavor":
            resource = method(provider_resource_id, get_extra_specs=False)
        else:
            resource = method(provider_resource_id)
    except (os_exc.ResourceNotFound, os_exc.NotFoundException) as exc:
        raise TargetedResourceNotFound("targeted resource not found") from exc
    if resource_type == "image":
        member_ids: list[Any] = []
        if str(_value(resource, "visibility", "")).lower() == "shared":
            budget = _EnrichmentBudget(enrichment_max_calls)
            budget.consume()
            member_ids = [_value(row, "member_id") for row in connection.image.members(resource)]
        return map_resource(
            resource_type,
            resource,
            member_project_ids=[value for value in member_ids if value],
        )
    if resource_type == "flavor":
        budget = _EnrichmentBudget(enrichment_max_calls)
        if not isinstance(_value(resource, "extra_specs"), Mapping):
            budget.consume()
            resource = connection.compute.fetch_flavor_extra_specs(resource) or resource
        access_ids: list[Any] = []
        if _bool(_value(resource, "is_public")) is False:
            budget.consume()
            access = connection.compute.get_flavor_access(resource)
            access_ids = [_value(row, "tenant_id") or _value(row, "project_id") for row in access]
        return map_resource(
            resource_type,
            resource,
            access_project_ids=[value for value in access_ids if value],
        )
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
