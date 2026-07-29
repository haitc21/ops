"""Provider service mapping for OpenStack error normalization."""

from __future__ import annotations

from ops.application.handlers.resource_operations import _provider_service_for_resource


def test_network_resources_map_to_network_service() -> None:
    assert _provider_service_for_resource("floating-ip") == "network"
    assert _provider_service_for_resource("network.floating_ip") == "network"
    assert _provider_service_for_resource("subnet") == "network"


def test_volume_resources_map_to_block_storage() -> None:
    assert _provider_service_for_resource("volume") == "block_storage"
    assert _provider_service_for_resource("volume-attachment") == "block_storage"


def test_identity_resources_map_to_identity_service() -> None:
    assert _provider_service_for_resource("domain") == "identity"
    assert _provider_service_for_resource("project") == "identity"
