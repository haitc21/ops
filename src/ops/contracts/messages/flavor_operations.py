"""Pinned CPS↔OPS Nova flavor lifecycle commands."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ops.contracts.messages.resource_operations import ScopeKind


class FlavorOperation(StrEnum):
    CREATE = "create"
    DELETE = "delete"
    REPLACE_ACCESS = "replace_access"
    PATCH_EXTRA_SPECS = "patch_extra_specs"


class FlavorOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default="1.0", pattern=r"^1\.0$")
    operation_id: UUID
    resource_type: str = Field(default="flavor", pattern=r"^flavor$")
    operation: FlavorOperation
    required_scope: ScopeKind = ScopeKind.SYSTEM
    provider_connection_id: UUID
    provider_resource_id: str | None = Field(default=None, min_length=1, max_length=255)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    vcpus: int | None = Field(default=None, ge=1, le=256)
    ram_mib: int | None = Field(default=None, ge=1, le=1048576)
    disk_gib: int | None = Field(default=None, ge=0, le=65536)
    ephemeral_gib: int = Field(default=0, ge=0, le=65536)
    swap_mib: int = Field(default=0, ge=0, le=1048576)
    is_public: bool | None = None
    access_project_ids: list[str] = Field(default_factory=list, max_length=1000)
    extra_specs: dict[str, str] = Field(default_factory=dict)
    remove_extra_spec_keys: list[str] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> FlavorOperationRequest:
        if self.operation is FlavorOperation.CREATE:
            if not self.name or self.vcpus is None or self.ram_mib is None or self.disk_gib is None:
                raise ValueError("flavor create requires name, vcpus, ram_mib, and disk_gib")
            if self.provider_resource_id == "auto":
                self.provider_resource_id = None
            if self.access_project_ids and self.is_public is not False:
                raise ValueError("access_project_ids require a private flavor")
        elif self.operation is FlavorOperation.DELETE:
            if not self.provider_resource_id:
                raise ValueError("flavor delete requires provider_resource_id")
        elif self.operation is FlavorOperation.REPLACE_ACCESS:
            if not self.provider_resource_id:
                raise ValueError("flavor access replacement requires provider_resource_id")
            if self.is_public is True and self.access_project_ids:
                raise ValueError("access_project_ids require a private flavor")
        elif not self.provider_resource_id:
            raise ValueError("flavor extra spec patch requires provider_resource_id")
        if len(set(self.access_project_ids)) != len(self.access_project_ids):
            raise ValueError("access_project_ids must be unique")
        if set(self.extra_specs).intersection(self.remove_extra_spec_keys):
            raise ValueError("extra spec keys cannot be updated and removed together")
        return self
