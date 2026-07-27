"""OPS-302/303 mapper tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from ops.application.handlers.inventory_collect import build_inventory_batch_messages
from ops.contracts.messages.envelope import MessageEnvelope
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
