from types import SimpleNamespace
from uuid import uuid4

import pytest
from openstack import exceptions as os_exc

from ops.application.handlers.resource_operations import _execute
from ops.contracts.messages.resource_operations import (
    ResourceOperationRequest,
    ResourceOperationState,
    ScopeKind,
)
from ops.contracts.messages.volume_operations import VolumeOperationRequest
from ops.openstack.volume_lifecycle import VolumeStateConflictError


class FakeBlockStorage:
    def __init__(self):
        self.items = []

    def create_volume(self, **kwargs):
        item = SimpleNamespace(
            id=f"volume-{len(self.items) + 1}",
            name=kwargs["name"],
            size=kwargs["size"],
            project_id=kwargs.get("project_id", "project-1"),
            attachments=[],
            bootable=False,
            status="available",
        )
        self.items.append(item)
        return item

    def get_volume(self, resource_id):
        for item in self.items:
            if item.id == resource_id:
                return item
        raise RuntimeError("not found")

    def extend_volume(self, item, new_size):
        item.size = new_size
        item.status = "extending"
        return item

    def wait_for_status(
        self,
        volume,
        *,
        status: str,
        failures: list[str] | None = None,
        interval: int | float | None = 2,
        wait: int | None = None,
        attribute: str = "status",
        callback=None,
    ):
        volume.status = status
        return volume

    def delete_volume(self, item, ignore_missing=False):
        if item.attachments or item.status in {"in-use", "detaching"}:
            raise os_exc.ConflictException("volume has attachments")
        self.items.remove(item)


def request(operation, **kwargs):
    return VolumeOperationRequest(
        operation_id=uuid4(),
        provider_connection_id=uuid4(),
        operation=operation,
        **kwargs,
    )


def connection(storage):
    return SimpleNamespace(
        block_storage=storage,
        session=SimpleNamespace(auth=SimpleNamespace(project_id="project-1")),
    )


def test_volume_create_resize_delete_uses_cinder_proxy_and_never_shrinks():
    storage = FakeBlockStorage()
    volume, state = _execute(
        connection(storage),
        request("create", name="data-01", size_gib=10, project_provider_resource_id="project-1"),
    )
    assert state.value == "SUCCEEDED"

    volume, state = _execute(
        connection(storage),
        request("resize", provider_resource_id=volume.id, size_gib=20),
    )
    assert volume.size == 20
    assert volume.status == "available"
    assert state.value == "SUCCEEDED"

    with pytest.raises(ValueError, match="cannot shrink"):
        _execute(
            connection(storage),
            request("resize", provider_resource_id=volume.id, size_gib=5),
        )

    _, state = _execute(connection(storage), request("delete", provider_resource_id=volume.id))
    assert state.value == "SUCCEEDED"


def test_volume_delete_rejects_detaching_volume() -> None:
    storage = FakeBlockStorage()
    volume, _ = _execute(
        connection(storage),
        request("create", name="data-01", size_gib=10, project_provider_resource_id="project-1"),
    )
    volume.status = "detaching"
    volume.attachments = [SimpleNamespace(server_id="server-1")]

    with pytest.raises(VolumeStateConflictError, match="cannot be deleted"):
        _execute(connection(storage), request("delete", provider_resource_id=volume.id))


class FakeCompute:
    def __init__(self) -> None:
        self.attach_calls: list[tuple[str, str]] = []
        self.detach_calls: list[tuple[str, str, bool]] = []
        self.detach_missing = False
        self.detach_conflicts = 0

    def get_server(self, server_id: str):
        return SimpleNamespace(id=server_id, project_id="project-1")

    def create_volume_attachment(self, server_id: str, volume_id: str):
        self.attach_calls.append((server_id, volume_id))
        return SimpleNamespace(id="attachment-1", server_id=server_id, volume_id=volume_id)

    def delete_volume_attachment(self, server_id: str, volume_id: str, *, ignore_missing: bool):
        self.detach_calls.append((server_id, volume_id, ignore_missing))
        if self.detach_conflicts:
            self.detach_conflicts -= 1
            raise os_exc.ConflictException("Nova is still using attachment")
        if self.detach_missing:
            raise os_exc.NotFoundException()


class AttachmentBlockStorage:
    def __init__(self, *, status: str = "available", attachments=None) -> None:
        self.status = status
        self.attachments = list(attachments or [])
        self.wait_calls: list[str] = []

    def get_volume(self, volume_id: str):
        return SimpleNamespace(
            id=volume_id,
            project_id="project-1",
            status=self.status,
            attachments=list(self.attachments),
        )

    def wait_for_status(
        self,
        volume,
        *,
        status: str,
        failures: list[str] | None = None,
        interval: int | float | None = 2,
        wait: int | None = None,
        attribute: str = "status",
        callback=None,
    ):
        self.wait_calls.append(status)
        self.status = status
        self.attachments = []
        volume.status = status
        volume.attachments = []
        return volume


def attachment_connection(compute: FakeCompute, storage: AttachmentBlockStorage | None = None):
    return SimpleNamespace(
        compute=compute,
        block_storage=storage or AttachmentBlockStorage(),
    )


def attachment_request(operation: str) -> ResourceOperationRequest:
    return ResourceOperationRequest(
        operation_id=uuid4(),
        provider_connection_id=uuid4(),
        resource_type="volume-attachment",
        operation=operation,
        required_scope=ScopeKind.PROJECT,
        parameters={
            "server_id": "server-1",
            "volume_id": "volume-1",
            "project_provider_resource_id": "project-1",
        },
    )


def test_volume_attachment_attach_waits_for_in_use() -> None:
    compute = FakeCompute()
    storage = AttachmentBlockStorage(status="attaching")
    attachment, state = _execute(
        attachment_connection(compute, storage),
        attachment_request("attach"),
    )
    assert state is ResourceOperationState.SUCCEEDED
    assert attachment.id == "attachment-1"
    assert compute.attach_calls == [("server-1", "volume-1")]
    assert storage.wait_calls == ["in-use"]


def test_volume_attachment_detach_waits_for_available_before_success() -> None:
    compute = FakeCompute()
    storage = AttachmentBlockStorage(
        status="detaching",
        attachments=[SimpleNamespace(server_id="server-1", device="/dev/vdc")],
    )
    volume, state = _execute(attachment_connection(compute, storage), attachment_request("detach"))
    assert volume.status == "available"
    assert volume.attachments == []
    assert state is ResourceOperationState.SUCCEEDED
    assert compute.detach_calls == [("server-1", "volume-1", True)]
    assert storage.wait_calls == ["available"]


def test_volume_attachment_detach_retries_transient_conflict(monkeypatch) -> None:
    compute = FakeCompute()
    compute.detach_conflicts = 1
    storage = AttachmentBlockStorage(
        status="detaching",
        attachments=[SimpleNamespace(server_id="server-1", device="/dev/vdc")],
    )
    monkeypatch.setattr(
        "ops.application.handlers.resource_operations.time.sleep", lambda _seconds: None
    )

    volume, state = _execute(attachment_connection(compute, storage), attachment_request("detach"))

    assert state is ResourceOperationState.SUCCEEDED
    assert volume.status == "available"
    assert len(compute.detach_calls) == 2


def test_volume_attachment_detach_surfaces_exhausted_conflicts(monkeypatch) -> None:
    compute = FakeCompute()
    compute.detach_conflicts = 3
    monkeypatch.setattr(
        "ops.application.handlers.resource_operations.time.sleep", lambda _seconds: None
    )

    with pytest.raises(os_exc.ConflictException):
        _execute(attachment_connection(compute), attachment_request("detach"))

    assert len(compute.detach_calls) == 3


def test_volume_attachment_detach_is_idempotent() -> None:
    compute = FakeCompute()
    compute.detach_missing = True
    storage = AttachmentBlockStorage(status="available", attachments=[])
    volume, state = _execute(attachment_connection(compute, storage), attachment_request("detach"))
    assert volume.status == "available"
    assert state is ResourceOperationState.ALREADY_ABSENT
    assert compute.detach_calls == [("server-1", "volume-1", True)]
    assert storage.wait_calls == []


def test_volume_attachment_rejects_cross_project_resources() -> None:
    compute = FakeCompute()
    block_storage = SimpleNamespace(
        get_volume=lambda _volume_id: SimpleNamespace(project_id="project-2")
    )
    with pytest.raises(ValueError, match="PROJECT_OWNERSHIP_MISMATCH"):
        _execute(
            SimpleNamespace(compute=compute, block_storage=block_storage),
            attachment_request("attach"),
        )
