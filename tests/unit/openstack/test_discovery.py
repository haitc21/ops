"""OPS-601 capability and API-version discovery tests."""

from __future__ import annotations

from types import SimpleNamespace

from ops.openstack.discovery import discover_capabilities


class _Config:
    def get_all_version_data(self, service_type: str) -> list[dict[str, object]]:
        return {
            "compute": [
                {
                    "version": (2, 1),
                    "min_microversion": (2, 1),
                    "max_microversion": (2, 90),
                }
            ],
            "network": [{"version": "2.0"}],
        }.get(service_type, [])


def _connection(*, include_block_storage: bool = True) -> SimpleNamespace:
    entries = [
        {"type": "identity", "endpoints": [{"url": "https://keystone.example"}]},
        {"type": "compute", "endpoints": [{"url": "https://nova.example"}]},
        {"type": "network", "endpoints": [{"url": "https://neutron.example"}]},
        {"type": "image", "endpoints": [{"url": "https://glance.example"}]},
    ]
    if include_block_storage:
        entries.append({"type": "block-storage", "endpoints": [{"url": "https://cinder.example"}]})
    compute = SimpleNamespace(
        create_server=lambda **_: None,
        start_server=lambda *_: None,
        stop_server=lambda *_: None,
        reboot_server=lambda *_args, **_kwargs: None,
        delete_server=lambda *_: None,
    )
    return SimpleNamespace(
        authorize=lambda: None,
        service_catalog=entries,
        config=_Config(),
        compute=compute,
    )


def test_discovery_reports_versions_microversions_and_operations() -> None:
    capabilities = discover_capabilities(_connection())

    assert capabilities.services["compute"].min_version == "2.1"
    assert capabilities.services["compute"].max_version == "2.1"
    assert capabilities.services["compute"].model_extra == {
        "endpoint": "https://nova.example",
        "min_microversion": "2.1",
        "max_microversion": "2.90",
    }
    assert capabilities.features["instance.create.image"].supported is True
    assert capabilities.features["instance.create.volume_from_image"].supported is True
    assert capabilities.features["instance.delete"].supported is True


def test_discovery_marks_optional_storage_features_unavailable() -> None:
    capabilities = discover_capabilities(_connection(include_block_storage=False))

    assert capabilities.services["block_storage"].available is False
    assert capabilities.features["service.block_storage"].supported is False
    assert capabilities.features["instance.create.volume_from_image"].supported is False


def test_discovery_requires_identity_and_compute() -> None:
    connection = _connection()
    connection.service_catalog = [
        {"type": "identity"},
    ]

    import pytest

    with pytest.raises(RuntimeError, match="identity and compute"):
        discover_capabilities(connection)
