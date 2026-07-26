from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from ops.application.handlers.instance_create import _create_kwargs, _find_server_by_operation
from ops.contracts.messages.instance import InstanceCommandPayload


def _payload(**overrides: object) -> InstanceCommandPayload:
    create = {
        "name": "demo",
        "flavor_provider_resource_id": "flavor-1",
        "boot_source": "IMAGE",
        "image_provider_resource_id": "image-1",
        "network_provider_resource_ids": ["network-1"],
    }
    create.update(overrides)
    return InstanceCommandPayload.model_validate({"action": "CREATE", "create": create})


def test_create_kwargs_injects_operation_marker_and_managed_keypair() -> None:
    operation_id = uuid.uuid4()
    kwargs = _create_kwargs(
        _payload(ssh_public_key="ssh-ed25519 " + "A" * 40),
        operation_id,
        key_name=f"cmp-{operation_id}",
    )

    assert kwargs["key_name"] == f"cmp-{operation_id}"
    assert kwargs["metadata"]["cmp_operation_id"] == str(operation_id)
    assert kwargs["metadata"]["cmp_keypair_name"] == f"cmp-{operation_id}"
    assert kwargs["metadata"]["cmp_ssh_username"] == "ubuntu"


def test_create_kwargs_does_not_put_private_key_material_in_request() -> None:
    kwargs = _create_kwargs(_payload(), uuid.uuid4())

    assert "private_key" not in kwargs
    assert "ssh_public_key" not in kwargs


@pytest.mark.asyncio
async def test_find_server_uses_operation_metadata_not_display_name() -> None:
    operation_id = uuid.uuid4()
    expected = SimpleNamespace(metadata={"cmp_operation_id": str(operation_id)})

    class Compute:
        def find_server(self, name: str, *, ignore_missing: bool) -> object | None:
            assert name == "user-visible-name"
            assert ignore_missing is True
            return expected

    result = await _find_server_by_operation(
        Compute(), operation_id, server_name="user-visible-name", timeout_seconds=1
    )

    assert result is expected
