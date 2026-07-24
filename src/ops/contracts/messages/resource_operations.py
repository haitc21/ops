"""Pinned CPS↔OPS provider-neutral scoped resource operation contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ScopeKind(StrEnum):
    SYSTEM = "SYSTEM"
    DOMAIN = "DOMAIN"
    PROJECT = "PROJECT"


class ResourceOperationState(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    ALREADY_ABSENT = "ALREADY_ABSENT"
    UNSUPPORTED = "UNSUPPORTED"
    FAILED = "FAILED"


class ResourceOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default="1.0", pattern=r"^1\.0$")
    operation_id: UUID
    resource_type: str = Field(min_length=1, max_length=64)
    operation: str = Field(min_length=1, max_length=64)
    required_scope: ScopeKind
    provider_connection_id: UUID
    provider_resource_id: str | None = Field(default=None, max_length=255)
    parameters: dict[str, Any] = Field(default_factory=dict)


class ResourceOperationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default="1.0", pattern=r"^1\.0$")
    operation_id: UUID
    resource_type: str = Field(min_length=1, max_length=64)
    operation: str = Field(min_length=1, max_length=64)
    state: ResourceOperationState
    required_scope: ScopeKind
    provider_connection_id: UUID
    provider_resource_id: str | None = Field(default=None, max_length=255)
    resource: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
