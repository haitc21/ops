"""Bounded Cinder volume state helpers for attach/detach/delete convergence."""

from __future__ import annotations

from typing import Any

from ops.contracts.errors import CommonError, ErrorCategory

_PUBLIC_MESSAGE = "OpenStack provider request failed"

VOLUME_DETACH_TARGET_STATUS = "available"
VOLUME_ATTACH_TARGET_STATUS = "in-use"
VOLUME_BLOCKED_DELETE_STATUSES = frozenset(
    {"in-use", "detaching", "attaching", "maintenance", "awaiting-transfer"}
)
VOLUME_WAIT_FAILURES = frozenset(
    {"error", "error_deleting", "error_attaching", "error_detaching", "error_extending"}
)
DEFAULT_VOLUME_WAIT_SECONDS = 300


class VolumeStateConflictError(RuntimeError):
    """Volume is in a state that blocks the requested lifecycle transition."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "INVALID_RESOURCE_STATE",
        status: str | None = None,
        resource_id: str | None = None,
        reason: str = "blocked_state",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.resource_id = resource_id
        self.reason = reason
        self.retryable = retryable


def normalize_volume_state_conflict(
    exc: VolumeStateConflictError,
    *,
    service: str = "block_storage",
) -> CommonError:
    details: dict[str, Any] = {"provider_reason": exc.reason}
    if exc.status is not None:
        details["provider_status"] = exc.status
    if exc.resource_id is not None:
        details["provider_resource_id"] = exc.resource_id
    return CommonError(
        code=exc.code,
        message=_PUBLIC_MESSAGE,
        category=ErrorCategory.CONFLICT,
        retryable=exc.retryable,
        provider="OPENSTACK",
        provider_service=service,
        provider_request_id=None,
        details=details,
    )


def _volume_status(volume: Any) -> str:
    return str(getattr(volume, "status", "")).lower()


def volume_has_server_attachment(volume: Any, server_id: str) -> bool:
    attachments = getattr(volume, "attachments", None) or []
    for attachment in attachments:
        attached_server = getattr(attachment, "server_id", None) or getattr(
            attachment, "instance_id", None
        )
        if attached_server is not None and str(attached_server) == server_id:
            return True
    return False


def assert_volume_deletable(volume: Any) -> None:
    status = _volume_status(volume)
    attachments = getattr(volume, "attachments", None) or []
    if status in VOLUME_BLOCKED_DELETE_STATUSES or attachments:
        raise VolumeStateConflictError(
            "volume cannot be deleted while attached or transitioning",
            status=status or None,
            resource_id=str(getattr(volume, "id", "")) or None,
            reason="volume_attached_or_transitioning",
        )


def assert_snapshot_force_for_in_use_volume(volume: Any, *, force: bool) -> None:
    if _volume_status(volume) == "in-use" and not force:
        raise VolumeStateConflictError(
            "snapshot create of an in-use volume requires force=true",
            status="in-use",
            resource_id=str(getattr(volume, "id", "")) or None,
            reason="snapshot_force_required",
            code="VOLUME_SNAPSHOT_FORCE_REQUIRED",
        )


def wait_for_volume_status(
    proxy: Any,
    volume: Any,
    *,
    target_status: str,
    wait_seconds: int = DEFAULT_VOLUME_WAIT_SECONDS,
) -> Any:
    """Poll Cinder until the volume reaches the requested status."""
    volume_id = str(getattr(volume, "id", ""))
    if not volume_id:
        raise ValueError("volume id is required")
    current_status = _volume_status(volume)
    if current_status == target_status.lower():
        return volume
    waiter = getattr(proxy, "wait_for_status", None)
    if not callable(waiter):
        refreshed = proxy.get_volume(volume_id)
        if _volume_status(refreshed) != target_status.lower():
            raise VolumeStateConflictError(
                "volume did not reach the requested status",
                status=_volume_status(refreshed) or None,
                resource_id=volume_id,
                reason="status_wait_unavailable",
            )
        return refreshed
    return waiter(
        volume,
        status=target_status,
        failures=sorted(VOLUME_WAIT_FAILURES),
        interval=1,
        wait=wait_seconds,
    )


def wait_for_volume_detached(
    proxy: Any,
    *,
    server_id: str,
    volume_id: str,
    wait_seconds: int = DEFAULT_VOLUME_WAIT_SECONDS,
) -> Any:
    """Wait until a volume is available and no longer attached to the server."""
    volume = proxy.get_volume(volume_id)
    if (
        not volume_has_server_attachment(volume, server_id)
        and _volume_status(volume) == VOLUME_DETACH_TARGET_STATUS
    ):
        return volume
    volume = wait_for_volume_status(
        proxy,
        volume,
        target_status=VOLUME_DETACH_TARGET_STATUS,
        wait_seconds=wait_seconds,
    )
    if volume_has_server_attachment(volume, server_id):
        raise VolumeStateConflictError(
            "volume detach did not converge",
            status=_volume_status(volume) or None,
            resource_id=volume_id,
            reason="attachment_still_present",
            retryable=True,
        )
    return volume
