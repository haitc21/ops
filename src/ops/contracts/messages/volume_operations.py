"""Provider-neutral standalone block-storage lifecycle contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ops.contracts.messages.resource_operations import ScopeKind


class VolumeOperation(StrEnum):
    CREATE = "create"
    RESIZE = "resize"
    DELETE = "delete"


class VolumeOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default="1.0", pattern=r"^1\.0$")
    operation_id: UUID
    resource_type: str = Field(default="volume", pattern=r"^volume$")
    operation: VolumeOperation
    required_scope: ScopeKind = ScopeKind.PROJECT
    provider_connection_id: UUID
    provider_resource_id: str | None = Field(default=None, max_length=255)
    project_provider_resource_id: str | None = Field(default=None, max_length=255)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    size_gib: int | None = Field(default=None, ge=1, le=16384)
    volume_type_provider_resource_id: str | None = Field(default=None, max_length=255)
    availability_zone: str | None = Field(default=None, max_length=255)
    metadata: dict[str, str] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> VolumeOperationRequest:
        if self.operation is VolumeOperation.CREATE:
            if not self.name:
                raise ValueError("volume create requires name")
            if self.size_gib is None:
                raise ValueError("volume create requires size_gib")
        elif self.operation is VolumeOperation.RESIZE:
            if not self.provider_resource_id:
                raise ValueError("volume resize requires provider_resource_id")
            if self.size_gib is None:
                raise ValueError("volume resize requires size_gib")
        elif not self.provider_resource_id:
            raise ValueError("volume delete requires provider_resource_id")
        return self
