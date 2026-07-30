from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from openstack import exceptions as os_exc

from ops.application.handlers.resource_operations import _execute
from ops.contracts.messages.resource_operations import (
    ResourceOperationRequest,
    ResourceOperationState,
)
from ops.openstack.volume_lifecycle import VolumeStateConflictError


class FakeSnapshotProxy:
    def __init__(self) -> None:
        self.items: dict[str, SimpleNamespace] = {}
        self.create_calls: list[dict[str, object]] = []
        self.update_calls: list[tuple[str, dict[str, object]]] = []
        self.delete_calls: list[tuple[str, bool, bool]] = []

    def snapshots(self, **_query):
        return iter(self.items.values())

    def get_volume(self, volume_id: str):
        if volume_id != "volume-1":
            raise os_exc.NotFoundException()
        return SimpleNamespace(id=volume_id, project_id="project-1")

    def get_snapshot(self, snapshot_id: str):
        try:
            return self.items[snapshot_id]
        except KeyError:
            raise os_exc.NotFoundException() from None

    def create_snapshot(self, **attrs):
        self.create_calls.append(attrs)
        snapshot = SimpleNamespace(
            id="snapshot-1",
            name=attrs["name"],
            volume_id=attrs["volume_id"],
            project_id=attrs.get("project_id", "project-1"),
            status="creating",
        )
        self.items[snapshot.id] = snapshot
        return snapshot

    def update_snapshot(self, snapshot, **attrs):
        self.update_calls.append((snapshot.id, attrs))
        for key, value in attrs.items():
            setattr(snapshot, key, value)
        return snapshot

    def delete_snapshot(self, snapshot, *, ignore_missing: bool, force: bool = False):
        self.delete_calls.append((snapshot.id, ignore_missing, force))
        self.items.pop(snapshot.id, None)


def request(operation: str, *, provider_resource_id: str | None = None, **parameters):
    return ResourceOperationRequest(
        operation_id=uuid4(),
        provider_connection_id=uuid4(),
        resource_type="snapshot",
        operation=operation,
        required_scope="PROJECT",
        provider_resource_id=provider_resource_id,
        parameters=parameters,
    )


def connection(proxy: FakeSnapshotProxy):
    return SimpleNamespace(
        block_storage=proxy,
        session=SimpleNamespace(auth=SimpleNamespace(project_id="project-1")),
    )


def test_snapshot_create_update_delete_uses_supported_sdk_proxy_methods() -> None:
    proxy = FakeSnapshotProxy()

    snapshot, state = _execute(
        connection(proxy),
        request(
            "create",
            volume_id="volume-1",
            name="before-upgrade",
            description="checkpoint",
            project_id="project-1",
        ),
    )
    assert state is ResourceOperationState.SUCCEEDED
    assert proxy.create_calls == [
        {
            "volume_id": "volume-1",
            "name": "before-upgrade",
            "description": "checkpoint",
        }
    ]

    updated, state = _execute(
        connection(proxy),
        request("update", provider_resource_id=snapshot.id, name="release"),
    )
    assert state is ResourceOperationState.SUCCEEDED
    assert updated.name == "release"
    assert proxy.update_calls == [("snapshot-1", {"name": "release"})]

    deleted, state = _execute(
        connection(proxy),
        request("delete", provider_resource_id=snapshot.id),
    )
    assert deleted is None
    assert state is ResourceOperationState.SUCCEEDED
    assert proxy.delete_calls == [("snapshot-1", True, False)]


def test_snapshot_delete_missing_is_idempotent() -> None:
    proxy = FakeSnapshotProxy()

    result, state = _execute(
        connection(proxy),
        request("delete", provider_resource_id="missing"),
    )

    assert result is None
    assert state is ResourceOperationState.ALREADY_ABSENT


def test_snapshot_create_requires_volume_id() -> None:
    proxy = FakeSnapshotProxy()

    with pytest.raises(ValueError, match="volume_id"):
        _execute(connection(proxy), request("create", name="missing-source"))


def test_snapshot_create_requires_force_for_in_use_volume() -> None:
    proxy = FakeSnapshotProxy()
    in_use_volume = SimpleNamespace(id="volume-in-use", project_id="project-1", status="in-use")
    proxy.get_volume = lambda volume_id: in_use_volume  # type: ignore[method-assign]

    with pytest.raises(VolumeStateConflictError, match="force=true"):
        _execute(
            connection(proxy),
            request("create", volume_id="volume-in-use", name="checkpoint", project_id="project-1"),
        )
