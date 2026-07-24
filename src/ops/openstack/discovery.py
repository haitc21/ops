"""Safe, provider-neutral OpenStack service and feature discovery."""

from __future__ import annotations

from typing import Any

from ops.contracts.validation import CapabilityDocument
from ops.openstack.scope import discover_effective_scope

_SERVICE_NAMES = ("identity", "compute", "network", "image", "block_storage")
_SERVICE_TYPES = {
    "identity": ("identity",),
    "compute": ("compute",),
    "network": ("network",),
    "image": ("image",),
    "block_storage": ("block-storage", "volumev3"),
}


class DiscoveryValidationError(RuntimeError):
    """The provider did not expose the required validation services."""


def _version_string(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, tuple):
        return ".".join(str(part) for part in value)
    text = str(value)
    return text or None


def _version_details(conn: Any, service: str) -> dict[str, object]:
    """Read SDK-negotiated version data without making raw HTTP calls."""
    config = getattr(conn, "config", None)
    get_versions = getattr(config, "get_all_version_data", None)
    if not callable(get_versions):
        return {}

    versions: list[Any] = []
    for service_type in _SERVICE_TYPES[service]:
        try:
            versions = list(get_versions(service_type) or [])
        except Exception:
            continue
        if versions:
            break
    if not versions:
        return {}

    api_versions = [
        version
        for version in (_version_string(item.get("version")) for item in versions)
        if version is not None
    ]
    details: dict[str, object] = {}
    if api_versions:
        details["min_version"] = min(
            api_versions, key=lambda value: tuple(int(part) for part in value.split("."))
        )
        details["max_version"] = max(
            api_versions, key=lambda value: tuple(int(part) for part in value.split("."))
        )

    min_microversions = [
        version
        for version in (_version_string(item.get("min_microversion")) for item in versions)
        if version is not None
    ]
    max_microversions = [
        version
        for version in (_version_string(item.get("max_microversion")) for item in versions)
        if version is not None
    ]
    if min_microversions:
        details["min_microversion"] = min_microversions[0]
    if max_microversions:
        details["max_microversion"] = max_microversions[-1]
    return details


def _feature(supported: bool, *, reason: str | None = None) -> dict[str, object]:
    result: dict[str, object] = {"supported": supported}
    if reason is not None:
        result["reason"] = reason
    return result


def discover_capabilities(conn: Any) -> CapabilityDocument:
    conn.authorize()
    catalog = getattr(conn, "service_catalog", []) or []
    available: dict[str, dict[str, object]] = {
        name: {"available": False, "reason": "SERVICE_NOT_AVAILABLE"} for name in _SERVICE_NAMES
    }
    for entry in catalog:
        if not isinstance(entry, dict):
            continue
        service_type = str(entry.get("type", ""))
        name = {
            "identity": "identity",
            "keystone": "identity",
            "compute": "compute",
            "nova": "compute",
            "network": "network",
            "neutron": "network",
            "image": "image",
            "glance": "image",
            "block-storage": "block_storage",
            "volumev3": "block_storage",
        }.get(service_type)
        if name is None:
            continue
        details: dict[str, object] = {"available": True}
        endpoints = entry.get("endpoints")
        if isinstance(endpoints, list) and endpoints:
            endpoint = endpoints[0]
            if isinstance(endpoint, dict) and isinstance(endpoint.get("url"), str):
                details["endpoint"] = endpoint["url"]
        details.update(_version_details(conn, name))
        available[name] = details
    if not available["identity"]["available"] or not available["compute"]["available"]:
        raise DiscoveryValidationError("identity and compute are required")
    features: dict[str, dict[str, object]] = {
        "connection.authenticate": _feature(True),
        "service.identity": _feature(True),
    }
    for service in _SERVICE_NAMES[1:]:
        is_available = bool(available[service]["available"])
        features[f"service.{service}"] = {
            **_feature(is_available, reason=None if is_available else "SERVICE_NOT_AVAILABLE"),
        }

    compute = getattr(conn, "compute", None)
    compute_features = {
        "instance.create.image": "create_server",
        "instance.start": "start_server",
        "instance.stop": "stop_server",
        "instance.reboot": "reboot_server",
        "instance.delete": "delete_server",
    }
    for feature, method_name in compute_features.items():
        supported = bool(available["compute"]["available"]) and callable(
            getattr(compute, method_name, None)
        )
        features[feature] = _feature(
            supported, reason=None if supported else "CAPABILITY_NOT_SUPPORTED"
        )
    volume_from_image = bool(available["compute"]["available"]) and bool(
        available["block_storage"]["available"]
    )
    features["instance.create.volume_from_image"] = _feature(
        volume_from_image,
        reason=None if volume_from_image else "SERVICE_NOT_AVAILABLE",
    )
    scope = discover_effective_scope(conn)
    for name, capability in scope["capabilities"].items():
        features[name] = _feature(bool(capability["supported"]), reason=capability.get("reason"))
    features["identity.scope.discover"] = _feature(True)
    return CapabilityDocument.model_validate(
        {
            "schema_version": "1.0",
            "services": available,
            "features": features,
            # CapabilityDocument permits additive fields; this is deliberately
            # primitive-only and contains no token or service catalog data.
            "scope": scope,
        }
    )
