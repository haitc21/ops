"""OPS-302/303 mapper tests."""

from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from openstack import exceptions as os_exc

from ops.application.handlers import inventory_collect as inventory_handler
from ops.application.handlers.inventory_collect import build_inventory_batch_messages
from ops.config import Settings
from ops.contracts.messages.envelope import MessageEnvelope
from ops.contracts.messages.inventory import InventoryBatchItem
from ops.messaging.consumer import HandlerRetryableError, HandlerSuccess
from ops.openstack.inventory import (
    TargetedResourceNotFound,
    collect_resources,
    collect_targeted_resource,
    map_resource,
)


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
    assert approved["attributes"]["catalog_approved"] is True
    assert rejected["attributes"]["catalog_approved"] is False


def test_glance_property_is_normalized_to_catalog_approval() -> None:
    item = map_resource(
        "image",
        SimpleNamespace(
            id="image-3",
            name="ubuntu",
            properties={"cmp-catalog-approved": "true"},
        ),
    )
    assert item["attributes"]["catalog_approved"] is True


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
    assert approved["attributes"]["catalog_approved"] is True
    assert rejected["attributes"]["catalog_approved"] is False


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


def test_enriched_catalog_batch_emits_schema_1_1_from_legacy_command() -> None:
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
    messages = build_inventory_batch_messages(
        command,
        sync_id=uuid.UUID("77777777-7777-4777-8777-777777777777"),
        resource_type="image",
        items=[
            {
                "provider_resource_id": "img-1",
                "name": "image",
                "visibility": "public",
                "attributes": {"catalog_approved": True},
            }
        ],
    )
    event = json.loads(messages[0][1])
    assert event["schema_version"] == "1.1"
    MessageEnvelope.model_validate(event)
    from jsonschema import Draft202012Validator, FormatChecker

    schema = json.loads(
        Path("src/ops/contracts/jsonschema/inventory_batch.schema.json").read_text()
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(event["payload"])


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


def test_image_mapper_emits_canonical_catalog_fields_and_sanitizes_properties() -> None:
    image = SimpleNamespace(
        id="img-1",
        name="ubuntu",
        status="ACTIVE",
        owner="project-1",
        visibility="PUBLIC",
        protected=False,
        container_format="BARE",
        disk_format="QCOW2",
        size=1024,
        virtual_size=2048,
        min_disk=5,
        min_ram=256,
        checksum="abc",
        tags=["z", "cmp-catalog-approved=true", "z"],
        properties={"os_type": "linux", "auth_token": "drop-me", "direct_url": "drop"},
    )
    item = map_resource("image", image)
    assert item["project_provider_resource_id"] == "project-1"
    assert item["visibility"] == "public"
    assert item["disk_format"] == "qcow2"
    assert item["size_bytes"] == 1024
    assert item["attributes"] == {
        "catalog_approved": True,
        "is_protected": False,
        "container_format": "bare",
        "virtual_size_bytes": 2048,
        "tags": ["cmp-catalog-approved=true", "z"],
        "properties": {"os_type": "linux"},
    }
    assert "drop-me" not in repr(item)


def test_image_mapper_normalizes_status_protection_and_members() -> None:
    item = map_resource(
        "image",
        SimpleNamespace(
            id="img-members",
            name="shared",
            status="ACTIVE",
            protected="false",
        ),
        member_project_ids=["project-2", "project-1", "project-2", "", None, "token=unsafe"],
    )
    assert item["provider_status"] == "active"
    assert item["attributes"]["is_protected"] is False
    assert item["attributes"]["member_project_ids"] == ["project-1", "project-2"]


def test_shared_image_collection_enriches_actual_member_rows_once() -> None:
    calls = {"images": 0, "members": 0}
    shared = SimpleNamespace(id="img-shared", name="shared", visibility="shared")
    public = SimpleNamespace(id="img-public", name="public", visibility="public")

    def images():
        calls["images"] += 1
        return [shared, public]

    def members(image):
        calls["members"] += 1
        assert image is shared
        return [
            SimpleNamespace(member_id="project-2"),
            SimpleNamespace(member_id="project-1"),
            SimpleNamespace(member_id="project-2"),
        ]

    items = collect_resources(
        SimpleNamespace(image=SimpleNamespace(images=images, members=members)), "image"
    )
    assert calls == {"images": 1, "members": 1}
    by_id = {item["provider_resource_id"]: item for item in items}
    assert by_id["img-shared"]["attributes"]["member_project_ids"] == [
        "project-1",
        "project-2",
    ]
    assert "member_project_ids" not in by_id["img-public"]["attributes"]


def test_shared_image_member_budget_fails_before_provider_call() -> None:
    calls = {"members": 0}

    def members(_image):
        calls["members"] += 1
        return []

    connection = SimpleNamespace(
        image=SimpleNamespace(
            images=lambda: [
                SimpleNamespace(id="img-1", name="shared-1", visibility="shared"),
                SimpleNamespace(id="img-2", name="shared-2", visibility="shared"),
            ],
            members=members,
        )
    )
    from ops.openstack.inventory import CatalogEnrichmentBudgetExceeded

    with pytest.raises(CatalogEnrichmentBudgetExceeded):
        collect_resources(connection, "image", enrichment_max_calls=1)
    assert calls["members"] == 1


def test_targeted_shared_image_uses_members_and_bounds_ids() -> None:
    image = SimpleNamespace(id="img", name="shared", visibility="shared")
    rows = [SimpleNamespace(member_id=f"project-{index:03}") for index in range(300)]
    rows.extend([SimpleNamespace(member_id="project-001"), SimpleNamespace(member_id="token=x")])
    connection = SimpleNamespace(
        image=SimpleNamespace(get_image=lambda _id: image, members=lambda actual: rows)
    )
    item = collect_targeted_resource(connection, "image", "img")
    members = item["attributes"]["member_project_ids"]
    assert len(members) == 256
    assert members == sorted(set(members))
    assert "token=x" not in members


@pytest.mark.parametrize(
    "error",
    [
        os_exc.ForbiddenException(),
        os_exc.ResourceNotFound(),
        os_exc.HttpException(http_status=429),
        os_exc.HttpException(http_status=500),
    ],
    ids=["forbidden", "concurrent-404", "rate-limited", "server-error"],
)
def test_shared_image_member_enrichment_errors_are_not_empty_success_or_tombstone(
    error,
) -> None:
    image = SimpleNamespace(id="img", name="shared", visibility="shared")

    def fail_members(_image):
        raise error

    connection = SimpleNamespace(
        image=SimpleNamespace(
            images=lambda: [image],
            get_image=lambda _id: image,
            members=fail_members,
        )
    )
    with pytest.raises(type(error)):
        collect_resources(connection, "image")
    with pytest.raises(type(error)):
        collect_targeted_resource(connection, "image", "img")


@pytest.mark.parametrize("visibility", ["private", "shared", "community"])
@pytest.mark.parametrize("disk_format", ["docker", "raw"])
def test_image_catalog_visibility_and_format_matrix(visibility, disk_format) -> None:
    item = map_resource(
        "image",
        SimpleNamespace(
            id=f"{visibility}-{disk_format}",
            name="image",
            visibility=visibility,
            disk_format=disk_format,
        ),
    )
    assert item["visibility"] == visibility
    assert item["disk_format"] == disk_format


def test_large_image_metadata_fails_contract_instead_of_publishing() -> None:
    item = map_resource(
        "image",
        SimpleNamespace(id="img-large", name="large", properties={"note": "x" * 70_000}),
    )
    with pytest.raises(ValueError, match="maximum"):
        InventoryBatchItem.model_validate(item)


@pytest.mark.parametrize(
    "field_update",
    [
        {"visibility": "internal"},
        {"size_bytes": -1},
        {"provider_status": {"raw": "ACTIVE"}},
        {"attributes": {"is_protected": "false"}},
    ],
    ids=["visibility", "negative-size", "object-status", "string-protection"],
)
def test_catalog_contract_rejects_malformed_provider_fields(field_update) -> None:
    item = {"provider_resource_id": "img-1", "name": "image", **field_update}
    with pytest.raises((ValueError, TypeError)):
        InventoryBatchItem.model_validate(item)


def test_image_mapper_drops_credential_and_signature_bearing_urls() -> None:
    item = map_resource(
        "image",
        SimpleNamespace(
            id="img-unsafe",
            name="unsafe",
            properties={
                "source": "https://user:pass@example.invalid/image",  # pragma: allowlist secret
                "signed": "https://example.invalid/image?X-Amz-Signature=synthetic",
                "homepage": "https://example.invalid/docs",
            },
        ),
    )
    assert item["attributes"]["properties"] == {"homepage": "https://example.invalid/docs"}


def test_flavor_collector_enriches_missing_specs_and_private_access_once() -> None:
    calls = {"list": 0, "specs": 0, "access": 0}
    flavor = SimpleNamespace(
        id="f-1",
        name="private",
        vcpus=2,
        ram=4096,
        disk=20,
        ephemeral=1,
        swap="",
        is_public=False,
        is_disabled=False,
    )

    def flavors(**kwargs: object) -> list[object]:
        calls["list"] += 1
        assert kwargs == {"details": True, "get_extra_specs": False}
        return [flavor]

    def specs(resource: object) -> object:
        calls["specs"] += 1
        resource.extra_specs = {"cmp-catalog-approved": "true", "hw:cpu_policy": "shared"}
        return resource

    def access(resource: object) -> list[object]:
        calls["access"] += 1
        return [SimpleNamespace(tenant_id="p-2"), SimpleNamespace(tenant_id="p-1")]

    connection = SimpleNamespace(
        compute=SimpleNamespace(
            flavors=flavors,
            fetch_flavor_extra_specs=specs,
            get_flavor_access=access,
        )
    )
    item = collect_resources(connection, "flavor")[0]
    assert calls == {"list": 1, "specs": 1, "access": 1}
    assert item["swap_mib"] == 0
    assert item["attributes"]["access_project_ids"] == ["p-1", "p-2"]
    assert item["attributes"]["catalog_approved"] is True


def test_flavor_enrichment_budget_fails_before_over_budget_call() -> None:
    calls = {"specs": 0, "access": 0}
    flavor = SimpleNamespace(id="f-1", name="private", is_public=False)

    def specs(resource: object) -> object:
        calls["specs"] += 1
        resource.extra_specs = {}
        return resource

    def access(_resource: object) -> list[object]:
        calls["access"] += 1
        return []

    connection = SimpleNamespace(
        compute=SimpleNamespace(
            flavors=lambda **_: [flavor],
            fetch_flavor_extra_specs=specs,
            get_flavor_access=access,
        )
    )
    from ops.openstack.inventory import CatalogEnrichmentBudgetExceeded

    with pytest.raises(CatalogEnrichmentBudgetExceeded):
        collect_resources(connection, "flavor", enrichment_max_calls=1)
    assert calls == {"specs": 1, "access": 0}


def test_targeted_flavor_base_404_is_distinct_from_enrichment_404() -> None:
    base_missing = SimpleNamespace(
        compute=SimpleNamespace(
            get_flavor=lambda *_args, **_kwargs: (_ for _ in ()).throw(os_exc.ResourceNotFound())
        )
    )
    with pytest.raises(TargetedResourceNotFound):
        collect_targeted_resource(base_missing, "flavor", "missing")

    flavor = SimpleNamespace(id="f-1", name="private", is_public=False, extra_specs={})
    enrichment_missing = SimpleNamespace(
        compute=SimpleNamespace(
            get_flavor=lambda *_args, **_kwargs: flavor,
            get_flavor_access=lambda *_: (_ for _ in ()).throw(os_exc.ResourceNotFound()),
        )
    )
    with pytest.raises(os_exc.ResourceNotFound):
        collect_targeted_resource(enrichment_missing, "flavor", "f-1")


@pytest.mark.parametrize(
    "error",
    [
        os_exc.ForbiddenException(),
        os_exc.HttpException(http_status=429),
        os_exc.HttpException(http_status=500),
    ],
    ids=["forbidden", "rate-limited", "server-error"],
)
def test_targeted_flavor_enrichment_failures_never_become_absence(error: Exception) -> None:
    flavor = SimpleNamespace(id="f-1", name="private", is_public=False, extra_specs={})
    connection = SimpleNamespace(
        compute=SimpleNamespace(
            get_flavor=lambda *_args, **_kwargs: flavor,
            get_flavor_access=lambda *_: (_ for _ in ()).throw(error),
        )
    )
    with pytest.raises(type(error)):
        collect_targeted_resource(connection, "flavor", "f-1")


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

    def stuck_collector(_connection, _resource_type, **_kwargs):
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_type"),
    [
        (os_exc.EndpointNotFound(), HandlerSuccess),
        (os_exc.ForbiddenException(), HandlerSuccess),
        (os_exc.HttpException(http_status=429), HandlerRetryableError),
        (os_exc.HttpException(http_status=500), HandlerRetryableError),
    ],
    ids=["service-absent", "forbidden", "rate-limited", "server-error"],
)
async def test_full_catalog_failure_classification(monkeypatch, error, expected_type) -> None:
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
            },
        }
    )

    async def resolve(_self, _connection_id):
        return SimpleNamespace()

    @contextmanager
    def fake_connection(_resolution, _settings):
        yield SimpleNamespace()

    def failing_collector(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(inventory_handler.CredentialResolver, "resolve", resolve)
    monkeypatch.setattr(inventory_handler, "openstack_connection", fake_connection)
    monkeypatch.setattr(inventory_handler, "collect_resources", failing_collector)
    outcome = await inventory_handler.inventory_collect(
        command, SimpleNamespace(), "ops.command.v1", settings=Settings(environment="test")
    )
    assert isinstance(outcome, expected_type)
    if isinstance(outcome, HandlerSuccess):
        batch = json.loads(outcome.result_messages[0][1])
        assert batch["payload"]["collection_status"] == "SKIPPED_UNSUPPORTED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_type", "deleted"),
    [
        (TargetedResourceNotFound("absent"), HandlerSuccess, True),
        (os_exc.ResourceNotFound(), HandlerRetryableError, False),
    ],
    ids=["base-getter-404", "post-get-enrichment-404"],
)
async def test_targeted_404_tombstone_boundary(monkeypatch, error, expected_type, deleted) -> None:
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
            "payload": {
                "sync_id": "77777777-7777-4777-8777-777777777777",
                "resource_type": "flavor",
                "provider_resource_id": "missing",
            },
        }
    )

    async def resolve(_self, _connection_id):
        return SimpleNamespace()

    @contextmanager
    def fake_connection(_resolution, _settings):
        yield SimpleNamespace()

    def failing_collector(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(inventory_handler.CredentialResolver, "resolve", resolve)
    monkeypatch.setattr(inventory_handler, "openstack_connection", fake_connection)
    monkeypatch.setattr(inventory_handler, "collect_targeted_resource", failing_collector)
    outcome = await inventory_handler.inventory_refresh(
        command, SimpleNamespace(), "ops.command.v1", settings=Settings(environment="test")
    )
    assert isinstance(outcome, expected_type)
    if deleted:
        assert isinstance(outcome, HandlerSuccess)
        batch = json.loads(outcome.result_messages[0][1])
        assert batch["payload"]["items"][0]["lifecycle_state"] == "DELETED"
    else:
        assert isinstance(outcome, HandlerRetryableError)
        assert not hasattr(outcome, "result_messages")
