"""Safe, provider-neutral OpenStack service and feature discovery."""

from __future__ import annotations

from typing import Any

from ops.contracts.validation import CapabilityDocument

_SERVICE_NAMES = ("identity", "compute", "network", "image", "block_storage")


class DiscoveryValidationError(RuntimeError):
    """The provider did not expose the required validation services."""


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
        available[name] = details
    if not available["identity"]["available"] or not available["compute"]["available"]:
        raise DiscoveryValidationError("identity and compute are required")
    features: dict[str, dict[str, object]] = {
        "connection.authenticate": {"supported": True},
        "service.identity": {"supported": True},
    }
    for service in _SERVICE_NAMES[1:]:
        is_available = bool(available[service]["available"])
        features[f"service.{service}"] = {
            "supported": is_available,
            **({} if is_available else {"reason": "SERVICE_NOT_AVAILABLE"}),
        }
    return CapabilityDocument.model_validate(
        {"schema_version": "1.0", "services": available, "features": features}
    )
