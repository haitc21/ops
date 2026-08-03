"""Pinned CPS-1904 instance snapshot contract tests."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from ops.contracts.messages.instance_snapshot_operations import InstanceSnapshotRequest


def test_snapshot_rejects_bytes_and_secret_metadata() -> None:
    with pytest.raises(ValidationError):
        InstanceSnapshotRequest.model_validate(
            {
                "operation_id": uuid.uuid4(),
                "provider_connection_id": uuid.uuid4(),
                "instance_provider_resource_id": "server-1",
                "project_provider_resource_id": "project-1",
                "name": "before-upgrade",
                "metadata": {"authorization": "unsafe"},
                "image_bytes": "forbidden",
            }
        )
