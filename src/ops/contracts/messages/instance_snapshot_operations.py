"""Pinned CPS↔OPS contract for no-bytes Nova instance snapshots."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

_SENSITIVE_METADATA_MARKERS = (
    "password",
    "secret",
    "token",
    "authorization",
    "private_key",
    "user_data",
)


class InstanceSnapshotRequest(BaseModel):
    """A snapshot request carries image metadata only; image data stays provider-side."""

    model_config = ConfigDict(extra="forbid")

    operation_id: UUID
    provider_connection_id: UUID
    instance_provider_resource_id: str = Field(min_length=1, max_length=255)
    project_provider_resource_id: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    metadata: dict[str, str] = Field(default_factory=dict, max_length=64)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, str]) -> dict[str, str]:
        for key, item in value.items():
            normalized_key = key.lower()
            if (
                not key
                or len(key) > 255
                or len(item) > 4096
                or any(marker in normalized_key for marker in _SENSITIVE_METADATA_MARKERS)
            ):
                raise ValueError("snapshot metadata contains an unsafe or oversized value")
        return value
