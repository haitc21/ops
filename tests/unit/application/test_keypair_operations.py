from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from openstack import exceptions as os_exc

from ops.application.handlers.resource_operations import _execute, _resource_payload
from ops.contracts.messages.resource_operations import (
    ResourceOperationRequest,
    ResourceOperationState,
)


class FakeCompute:
    def __init__(self) -> None:
        self.items: dict[str, SimpleNamespace] = {}
        self.created: list[dict[str, object]] = []
        self.deleted: list[str] = []

    def find_keypair(self, name: str, *, ignore_missing: bool):
        return next((item for item in self.items.values() if item.name == name), None)

    def create_keypair(self, **attrs):
        item = SimpleNamespace(
            id="key-1",
            name=attrs["name"],
            public_key=attrs["public_key"],
            fingerprint="fp-1",
            type="ssh-ed25519",
            project_id="project-1",
        )
        self.created.append(attrs)
        self.items[item.id] = item
        return item

    def get_keypair(self, resource_id: str):
        if resource_id not in self.items:
            raise os_exc.NotFoundException()
        return self.items[resource_id]

    def delete_keypair(self, resource, *, ignore_missing: bool):
        self.deleted.append(resource.id)
        self.items.pop(resource.id, None)


def make_request(operation: str, *, provider_resource_id: str | None = None, **params):
    return ResourceOperationRequest(
        operation_id=uuid4(),
        provider_connection_id=uuid4(),
        resource_type="keypair",
        operation=operation,
        required_scope="PROJECT",
        provider_resource_id=provider_resource_id,
        parameters=params,
    )


def make_connection(compute: FakeCompute):
    return SimpleNamespace(
        compute=compute,
        session=SimpleNamespace(auth=SimpleNamespace(project_id="project-1")),
    )


def test_keypair_import_is_idempotent_and_delete_is_convergent() -> None:
    compute = FakeCompute()
    public_key = "ssh-ed25519 " + "A" * 64
    resource, state = _execute(
        make_connection(compute),
        make_request("create", name="cmp-key", public_key=public_key, project_id="project-1"),
    )
    assert state is ResourceOperationState.SUCCEEDED
    assert compute.created == [{"name": "cmp-key", "public_key": public_key}]

    replay, state = _execute(
        make_connection(compute),
        make_request("create", name="cmp-key", public_key=public_key, project_id="project-1"),
    )
    assert replay.id == resource.id
    assert len(compute.created) == 1

    deleted, state = _execute(
        make_connection(compute),
        make_request("delete", provider_resource_id="key-1", project_id="project-1"),
    )
    assert deleted is None
    assert state is ResourceOperationState.SUCCEEDED


def test_keypair_rejects_private_material_and_name_collision() -> None:
    compute = FakeCompute()
    with pytest.raises(ValueError, match="PRIVATE_KEY_MATERIAL_REJECTED"):
        _execute(
            make_connection(compute),
            make_request(
                "create",
                name="bad",
                public_key="-----BEGIN OPENSSH PRIVATE KEY-----"  # pragma: allowlist secret
                + "A" * 64,
            ),
        )
    _execute(
        make_connection(compute),
        make_request(
            "create", name="cmp-key", public_key="ssh-ed25519 " + "A" * 64, project_id="project-1"
        ),
    )
    with pytest.raises(ValueError, match="KEYPAIR_NAME_CONFLICT"):
        _execute(
            make_connection(compute),
            make_request(
                "create",
                name="cmp-key",
                public_key="ssh-ed25519 " + "B" * 64,
                project_id="project-1",
            ),
        )


def test_keypair_result_mapper_drops_private_material_from_sdk_mapping() -> None:
    payload = _resource_payload(
        make_request("create"),
        {
            "id": "key-1",
            "name": "cmp-key",
            "fingerprint": "fp-1",
            "type": "ssh-ed25519",
            "public_key": "ssh-ed25519 " + "A" * 64,
            "private_key": "-----BEGIN OPENSSH PRIVATE KEY-----",  # pragma: allowlist secret
            "user_id": "user-1",
        },
        "keypair",
    )
    assert "private_key" not in repr(payload)
    assert payload["provider_resource_id"] == "key-1"
    assert payload["attributes"]["public_key"].startswith("ssh-ed25519 ")
