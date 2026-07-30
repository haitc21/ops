"""Regression tests for bounded Cinder volume lifecycle convergence."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from openstack import exceptions as os_exc

from ops.openstack.volume_lifecycle import (
    VolumeStateConflictError,
    assert_snapshot_force_for_in_use_volume,
    assert_volume_deletable,
    normalize_volume_state_conflict,
    volume_has_server_attachment,
    wait_for_volume_detached,
    wait_for_volume_status,
)


def test_assert_volume_deletable_rejects_detaching_volume() -> None:
    volume = SimpleNamespace(
        id="volume-1",
        status="detaching",
        attachments=[SimpleNamespace(server_id="server-1")],
    )

    with pytest.raises(VolumeStateConflictError, match="cannot be deleted"):
        assert_volume_deletable(volume)


def test_assert_volume_deletable_allows_available_volume() -> None:
    assert_volume_deletable(SimpleNamespace(id="volume-1", status="available", attachments=[]))


def test_snapshot_force_required_for_in_use_volume() -> None:
    volume = SimpleNamespace(id="volume-1", status="in-use")

    with pytest.raises(VolumeStateConflictError, match="force=true"):
        assert_snapshot_force_for_in_use_volume(volume, force=False)


def test_snapshot_force_allows_in_use_volume_when_forced() -> None:
    volume = SimpleNamespace(id="volume-1", status="in-use")
    assert_snapshot_force_for_in_use_volume(volume, force=True)


def test_normalize_volume_state_conflict_preserves_provider_status() -> None:
    exc = VolumeStateConflictError(
        "blocked",
        status="detaching",
        resource_id="volume-1",
        reason="volume_attached_or_transitioning",
    )

    error = normalize_volume_state_conflict(exc)

    assert error.code == "INVALID_RESOURCE_STATE"
    assert error.category.value == "CONFLICT"
    assert error.retryable is False
    assert error.details == {
        "provider_reason": "volume_attached_or_transitioning",
        "provider_status": "detaching",
        "provider_resource_id": "volume-1",
    }


class FakeVolumeProxy:
    def __init__(self, volume: SimpleNamespace) -> None:
        self.volume = volume
        self.wait_calls: list[tuple[str, int]] = []

    def get_volume(self, volume_id: str):
        if volume_id != self.volume.id:
            raise os_exc.NotFoundException()
        return self.volume

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
        self.wait_calls.append((status, wait or 0))
        self.volume.status = status
        self.volume.attachments = []
        return self.volume


def test_wait_for_volume_detached_waits_until_available_without_attachment() -> None:
    volume = SimpleNamespace(
        id="volume-1",
        status="detaching",
        attachments=[SimpleNamespace(server_id="server-1", device="/dev/vdc")],
    )
    proxy = FakeVolumeProxy(volume)

    result = wait_for_volume_detached(proxy, server_id="server-1", volume_id="volume-1")

    assert result.status == "available"
    assert volume_has_server_attachment(result, "server-1") is False
    assert proxy.wait_calls == [("available", 300)]


def test_wait_for_volume_status_returns_immediately_when_already_target() -> None:
    volume = SimpleNamespace(id="volume-1", status="available", attachments=[])
    proxy = FakeVolumeProxy(volume)

    result = wait_for_volume_status(proxy, volume, target_status="available")

    assert result.status == "available"
    assert proxy.wait_calls == []
