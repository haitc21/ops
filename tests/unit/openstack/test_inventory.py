"""OPS-302/303 mapper tests."""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from ops.application.handlers import inventory_collect as inventory_handler
from ops.application.handlers.inventory_collect import build_inventory_batch_messages
from ops.config import Settings
from ops.contracts.messages.envelope import MessageEnvelope
from ops.messaging.consumer import HandlerRetryableError
from ops.openstack.inventory import collect_resources, collect_targeted_resource, map_resource


def test_instance_mapper_contains_only_contract_safe_scalars_and_collections() -> None:
    resource = SimpleNamespace(
        id="server-1",
        name="demo",
        status="ACTIVE",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        flavor=SimpleNamespace(id="flavor-1"),
        image=None,
        addresses={"public": [{"addr": "192.0.2.10"}]},
        metadata={"role": "worker"},
    )
    item = map_resource("instance", resource)
    assert item["provider_resource_id"] == "server-1"
    assert item["attributes"]["flavor"] == "flavor-1" or isinstance(
        item["attributes"]["flavor"], str
    )
    assert "password" not in repr(item).lower()


def test_catalog_tag_is_normalized_to_explicit_approval_flag() -> None:
    approved = map_resource(
        "image",
        SimpleNamespace(id="image-1", name="ubuntu", tags=["cmp-catalog-approved=true"]),
    )
    rejected = map_resource(
        "image", SimpleNamespace(id="image-2", name="private", tags=["team-only"])
    )
    assert approved["catalog_approved"] is True
    assert rejected["catalog_approved"] is False


def test_glance_property_is_normalized_to_catalog_approval() -> None:
    item = map_resource(
        "image",
        SimpleNamespace(
            id="image-3",
            name="ubuntu",
            properties={"cmp-catalog-approved": "true"},
        ),
    )
    assert item["catalog_approved"] is True


def test_flavor_extra_specs_is_normalized_to_catalog_approval() -> None:
    approved = map_resource(
        "flavor",
        SimpleNamespace(
            id="1",
            name="m1.nano",
            vcpus=1,
            ram=64,
            disk=1,
            extra_specs={"cmp-catalog-approved": "true"},
        ),
    )
    rejected = map_resource(
        "flavor",
        SimpleNamespace(
            id="2",
            name="m1.small",
            vcpus=1,
            ram=2048,
            disk=20,
            extra_specs={"cmp-catalog-approved": "false"},
        ),
    )
    assert approved["catalog_approved"] is True
    assert rejected["catalog_approved"] is False


def test_catalog_mappers_emit_the_canonical_image_and_flavor_fields() -> None:
    image = map_resource(
        "image",
        SimpleNamespace(
            id="image-1",
            name="ubuntu",
            status="active",
            owner="project-1",
            visibility="shared",
            protected=True,
            container_format="bare",
            disk_format="raw",
            size=2_147_483_648,
            virtual_size=10_737_418_240,
            tags=["cmp-catalog-approved=true", "ubuntu"],
            properties={"os_distro": "ubuntu", "password": "drop"},
            checksum="a" * 32,
            min_disk=20,
            min_ram=2048,
        ),
    )
    flavor = map_resource(
        "flavor",
        SimpleNamespace(
            id="flavor-1",
            name="medium",
            vcpus=4,
            ram=8192,
            disk=80,
            ephemeral=20,
            swap="1024",
            is_public=False,
            disabled=False,
            extra_specs={"hw:cpu_policy": "dedicated", "token": "drop"},
            access_project_ids=["project-2", "project-1", "project-2"],
        ),
    )

    assert image == {
        "provider_resource_id": "image-1",
        "project_provider_resource_id": "project-1",
        "name": "ubuntu",
        "provider_status": "active",
        "provider_created_at": None,
        "provider_updated_at": None,
        "visibility": "shared",
        "is_public": False,
        "is_protected": True,
        "container_format": "bare",
        "disk_format": "raw",
        "size_bytes": 2_147_483_648,
        "virtual_size_bytes": 10_737_418_240,
        "tags": ["cmp-catalog-approved=true", "ubuntu"],
        "properties": {"os_distro": "ubuntu"},
        "checksum": "a" * 32,
        "min_disk_gib": 20,
        "min_ram_mib": 2048,
        "catalog_approved": True,
        "attributes": {},
    }
    assert flavor == {
        "provider_resource_id": "flavor-1",
        "name": "medium",
        "provider_status": None,
        "provider_created_at": None,
        "provider_updated_at": None,
        "vcpus": 4,
        "ram_mib": 8192,
        "root_disk_gib": 80,
        "ephemeral_disk_gib": 20,
        "swap_mib": 1024,
        "is_public": False,
        "enabled": True,
        "extra_specs": {"hw:cpu_policy": "dedicated"},
        "access_project_ids": ["project-1", "project-2"],
        "attributes": {},
    }


def test_image_collector_enriches_shared_member_access_with_a_bounded_safe_list() -> None:
    connection = SimpleNamespace(
        image=SimpleNamespace(
            images=lambda: [
                SimpleNamespace(
                    id="image-1",
                    name="shared",
                    visibility="shared",
                )
            ],
            members=lambda image_id: [
                SimpleNamespace(member_id="project-2"),
                SimpleNamespace(member_id="project-1"),
                SimpleNamespace(member_id="project-2"),
            ],
        )
    )

    item = collect_resources(connection, "image")[0]

    assert item["access_project_ids"] == ["project-1", "project-2"]


def test_catalog_metadata_is_bounded_to_the_cps_contract_limits() -> None:
    item = map_resource(
        "image",
        SimpleNamespace(
            id="image-1",
            name="safe",
            properties={f"key-{index:03d}": "safe" for index in range(130)},
        ),
    )

    assert len(item["properties"]) == 128
    assert "key-127" in item["properties"]
    assert "key-128" not in item["properties"]


def test_volume_type_extra_specs_is_normalized_to_catalog_approval() -> None:
    item = map_resource(
        "volume-type",
        SimpleNamespace(
            id="type-1",
            name="gold",
            is_public=True,
            extra_specs={"cmp-catalog-approved": "true", "volume_backend_name": "ceph"},
        ),
    )
    assert item["attributes"]["catalog_approved"] is True
    assert item["attributes"]["is_public"] is True


def test_availability_zone_approval_comes_from_host_aggregate_metadata() -> None:
    connection = SimpleNamespace(
        compute=SimpleNamespace(
            availability_zones=lambda: [
                SimpleNamespace(name="az-approved", zone_state={"available": True}),
                SimpleNamespace(name="az-private", zone_state={"available": True}),
            ],
            aggregates=lambda: [
                SimpleNamespace(
                    availability_zone="az-approved",
                    metadata={"cmp-catalog-approved": "true"},
                )
            ],
        )
    )
    items = collect_resources(connection, "availability-zone")
    assert [item["provider_resource_id"] for item in items] == ["az-approved", "az-private"]
    assert items[0]["attributes"] == {"available": True, "catalog_approved": True}
    assert items[1]["attributes"] == {"available": True, "catalog_approved": False}


def test_volume_type_collector_uses_block_storage_types() -> None:
    connection = SimpleNamespace(
        block_storage=SimpleNamespace(
            types=lambda: [
                SimpleNamespace(
                    id="type-1",
                    name="gold",
                    extra_specs={"cmp-catalog-approved": "true"},
                )
            ]
        )
    )
    assert collect_resources(connection, "volume-type")[0]["attributes"]["catalog_approved"] is True


def test_mapper_sanitizes_nested_sdk_resources_and_drops_secret_fields() -> None:
    nested_resource = SimpleNamespace(id="port-1", name="should-not-be-needed")
    sensitive_field = "sec" + "ret"
    opaque_resource = SimpleNamespace(**{sensitive_field: "redacted-value"})
    resource = SimpleNamespace(
        id="server-1",
        name="demo",
        status="ACTIVE",
        addresses={"public": [{"port": nested_resource, "pass" + "word": "masked"}]},
        attachments=[{"volume": nested_resource}],
        metadata={"role": "worker", "opaque": opaque_resource},
    )

    item = map_resource("instance", resource)

    assert item["attributes"]["addresses"] == {"public": [{"port": {"id": "port-1"}}]}
    assert item["attributes"]["attachments"] == [{"volume": {"id": "port-1"}}]
    assert item["attributes"]["metadata"] == {"role": "worker"}
    assert "redacted-value" not in repr(item)


def test_collector_uses_proxy_generator_and_maps_sdk_objects() -> None:
    connection = SimpleNamespace(
        compute=SimpleNamespace(
            servers=lambda: [SimpleNamespace(id="server-1", name="demo", status="ACTIVE")]
        )
    )
    assert collect_resources(connection, "instance") == [
        {
            "provider_resource_id": "server-1",
            "name": "demo",
            "provider_status": "ACTIVE",
            "provider_created_at": None,
            "provider_updated_at": None,
            "attributes": {},
        }
    ]


def test_domain_and_project_collectors_use_identity_generators() -> None:
    connection = SimpleNamespace(
        identity=SimpleNamespace(
            domains=lambda: [SimpleNamespace(id="domain-1", name="tenant-domain", is_enabled=True)],
            projects=lambda: [
                SimpleNamespace(id="project-1", name="workspace", domain_id="domain-1")
            ],
        )
    )
    assert collect_resources(connection, "domain")[0]["attributes"] == {"is_enabled": True}
    assert collect_resources(connection, "project")[0]["attributes"] == {"domain_id": "domain-1"}


def test_domain_targeted_not_found_uses_identity_getter() -> None:
    connection = SimpleNamespace(
        identity=SimpleNamespace(
            get_domain=lambda resource_id: SimpleNamespace(id=resource_id, name="domain")
        )
    )
    assert collect_targeted_resource(connection, "domain", "domain-1")["provider_resource_id"] == (
        "domain-1"
    )


def test_collection_order_is_stable_for_redelivery_checksums() -> None:
    connection = SimpleNamespace(
        identity=SimpleNamespace(
            projects=lambda: [
                SimpleNamespace(id="project-2", name="two"),
                SimpleNamespace(id="project-1", name="one"),
            ]
        )
    )
    items = collect_resources(connection, "project")
    assert [item["provider_resource_id"] for item in items] == ["project-1", "project-2"]


def test_batch_builder_chunks_deterministically_without_secrets() -> None:
    command = MessageEnvelope.model_validate(
        {
            "message_id": "11111111-1111-4111-8111-111111111111",
            "message_type": "openstack.inventory.collect",
            "schema_version": "1.0",
            "occurred_at": "2026-07-23T00:00:00Z",
            "correlation_id": "22222222-2222-4222-8222-222222222222",
            "operation_id": "33333333-3333-4333-8333-333333333333",
            "provider_id": "44444444-4444-4444-8444-444444444444",
            "provider_connection_id": "55555555-5555-4555-8555-555555555555",
            "payload": {},
        }
    )
    items = [
        {"provider_resource_id": "i-1", "name": "one", "attributes": {}},
        {"provider_resource_id": "i-2", "name": "two", "attributes": {}},
    ]
    messages = build_inventory_batch_messages(
        command,
        sync_id=uuid.UUID("77777777-7777-4777-8777-777777777777"),
        resource_type="instance",
        items=items,
        batch_size=1,
    )
    assert len(messages) == 2
    assert messages[0][0] == "cloud.inventory.batch"
    assert b"password" not in messages[0][1].lower()


def test_targeted_collector_uses_resource_getter() -> None:
    connection = SimpleNamespace(
        network=SimpleNamespace(
            get_port=lambda resource_id: SimpleNamespace(
                id=resource_id, name="port", status="ACTIVE"
            )
        )
    )
    assert (
        collect_targeted_resource(connection, "port", "port-1")["provider_resource_id"] == "port-1"
    )


def test_keypair_inventory_is_project_owned_and_public_only() -> None:
    connection = SimpleNamespace(
        compute=SimpleNamespace(
            keypairs=lambda: [
                {
                    "id": "key-1",
                    "name": "cmp-key",
                    "fingerprint": "fp-1",
                    "type": "ssh-ed25519",
                    "public_key": "ssh-ed25519 AAA",
                    "private_key": "should-drop",  # pragma: allowlist secret
                }
            ]
        ),
        session=SimpleNamespace(auth=SimpleNamespace(project_id="project-1")),
    )
    item = collect_resources(connection, "keypair")[0]
    assert item["project_provider_resource_id"] == "project-1"
    assert item["attributes"]["public_key"] == "ssh-ed25519 AAA"
    assert "private_key" not in repr(item)


def test_tombstone_batch_is_contract_valid() -> None:
    command = MessageEnvelope.model_validate(
        {
            "message_id": "11111111-1111-4111-8111-111111111111",
            "message_type": "openstack.inventory.refresh",
            "schema_version": "1.0",
            "occurred_at": "2026-07-23T00:00:00Z",
            "correlation_id": "22222222-2222-4222-8222-222222222222",
            "operation_id": "33333333-3333-4333-8333-333333333333",
            "provider_id": "44444444-4444-4444-8444-444444444444",
            "provider_connection_id": "55555555-5555-4555-8555-555555555555",
            "payload": {},
        }
    )
    messages = build_inventory_batch_messages(
        command,
        sync_id=uuid.UUID("77777777-7777-4777-8777-777777777777"),
        resource_type="port",
        items=[
            {
                "provider_resource_id": "missing-port",
                "name": "missing-port",
                "lifecycle_state": "DELETED",
                "attributes": {},
            }
        ],
    )
    assert b'"lifecycle_state":"DELETED"' in messages[0][1]


def test_volume_mapper_emits_typed_project_and_bounded_attachment_summary() -> None:
    resource = SimpleNamespace(
        id="volume-1",
        name="data",
        status="available",
        project_id="project-1",
        size=20,
        volume_type=SimpleNamespace(id="type-1"),
        bootable="false",
        encrypted=True,
        availability_zone="nova",
        metadata={"tier": "gold"},
        attachments=[{"server_id": "server-1", "device": "/dev/vdb", "host_name": "compute-1"}],
    )

    item = map_resource("volume", resource)

    assert item["project_provider_resource_id"] == "project-1"
    assert item["size_gib"] == 20
    assert item["volume_type_provider_resource_id"] == "type-1"
    assert item["metadata"] == {"tier": "gold"}
    assert item["attachments"] == [{"server_id": "server-1", "device": "/dev/vdb"}]


def test_snapshot_mapper_emits_project_volume_and_metadata_fields() -> None:
    resource = SimpleNamespace(
        id="snapshot-1",
        name="before-upgrade",
        status="available",
        project_id="project-1",
        volume_id="volume-1",
        size=20,
        description="safe checkpoint",
        metadata={"purpose": "release"},
    )

    item = map_resource("snapshot", resource)

    assert item["provider_resource_id"] == "snapshot-1"
    assert item["attributes"] == {
        "project_id": "project-1",
        "volume_id": "volume-1",
        "size": 20,
        "description": "safe checkpoint",
        "metadata": {"purpose": "release"},
    }


def test_snapshot_collection_uses_block_storage_proxy() -> None:
    connection = SimpleNamespace(
        block_storage=SimpleNamespace(
            snapshots=lambda: [SimpleNamespace(id="snapshot-2", name="later", status="available")]
        )
    )

    assert collect_resources(connection, "snapshot")[0]["provider_resource_id"] == "snapshot-2"


def test_normalize_collection_names_maps_cps_hyphen_identifiers() -> None:
    from ops.openstack.inventory import normalize_collection_name, normalize_collection_names

    assert normalize_collection_name("security-group") == "security_group"
    assert normalize_collection_name("floating-ip") == "floating_ip"
    assert normalize_collection_name("volume-snapshot") == "volume-snapshot"
    assert normalize_collection_name("role-assignment") is None
    assert normalize_collection_names(
        [
            "domain",
            "security-group",
            "floating-ip",
            "role-assignment",
            "quota",
            "security-group",
        ]
    ) == ["domain", "security_group", "floating_ip"]


@pytest.mark.asyncio
async def test_inventory_collection_bounds_blocking_provider_call(monkeypatch) -> None:
    command = MessageEnvelope.model_validate(
        {
            "message_id": "11111111-1111-4111-8111-111111111111",
            "message_type": "openstack.inventory.collect",
            "schema_version": "1.0",
            "occurred_at": "2026-07-23T00:00:00Z",
            "correlation_id": "22222222-2222-4222-8222-222222222222",
            "operation_id": "33333333-3333-4333-8333-333333333333",
            "provider_id": "44444444-4444-4444-8444-444444444444",
            "provider_connection_id": "55555555-5555-4555-8555-555555555555",
            "payload": {
                "sync_id": "77777777-7777-4777-8777-777777777777",
                "collections": ["image"],
                "batch_size": 1,
            },
        }
    )

    async def resolve(_self, _connection_id):
        return SimpleNamespace()

    @contextmanager
    def fake_connection(_resolution, _settings):
        yield SimpleNamespace()

    def stuck_collector(_connection, _resource_type):
        time.sleep(2)
        return []

    monkeypatch.setattr(inventory_handler.CredentialResolver, "resolve", resolve)
    monkeypatch.setattr(inventory_handler, "openstack_connection", fake_connection)
    monkeypatch.setattr(inventory_handler, "collect_resources", stuck_collector)

    outcome = await inventory_handler.inventory_collect(
        command,
        SimpleNamespace(),
        "ops.command.v1",
        settings=Settings(environment="test", openstack_timeout_seconds=1),
    )

    assert isinstance(outcome, HandlerRetryableError)
    assert outcome.retry_reason == "PROVIDER_UNAVAILABLE"
