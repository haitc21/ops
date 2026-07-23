"""Canonical CPS↔OPS VM lifecycle contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InstanceBootSource(StrEnum):
    IMAGE = "IMAGE"
    VOLUME_FROM_IMAGE = "VOLUME_FROM_IMAGE"


class InstanceAction(StrEnum):
    CREATE = "CREATE"
    GET = "GET"
    START = "START"
    STOP = "STOP"
    REBOOT = "REBOOT"
    DELETE = "DELETE"


class InstanceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    flavor_provider_resource_id: str = Field(min_length=1, max_length=255)
    boot_source: InstanceBootSource
    image_provider_resource_id: str = Field(min_length=1, max_length=255)
    network_provider_resource_ids: list[str] = Field(default_factory=list, max_length=32)
    port_provider_resource_ids: list[str] = Field(default_factory=list, max_length=32)
    security_group_provider_resource_ids: list[str] = Field(default_factory=list, max_length=64)
    key_name: str | None = Field(default=None, max_length=255)
    availability_zone: str | None = Field(default=None, max_length=255)
    user_data: str | None = Field(default=None, max_length=65536)
    config_drive: bool = False
    metadata: dict[str, str] = Field(default_factory=dict, max_length=64)
    root_volume_size_gib: int | None = Field(default=None, ge=1, le=16384)
    delete_on_termination: bool = True

    @model_validator(mode="after")
    def validate_boot_and_network(self) -> InstanceCreateRequest:
        if not self.network_provider_resource_ids and not self.port_provider_resource_ids:
            raise ValueError("at least one explicit network or port is required")
        if self.boot_source is InstanceBootSource.IMAGE and self.root_volume_size_gib is not None:
            raise ValueError("root volume size is only valid for VOLUME_FROM_IMAGE")
        return self


class InstanceCommandPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: InstanceAction
    instance_provider_resource_id: str | None = Field(default=None, max_length=255)
    create: InstanceCreateRequest | None = None
    reboot_type: str | None = Field(default=None, pattern="^(SOFT|HARD)$")

    @model_validator(mode="after")
    def validate_action_payload(self) -> InstanceCommandPayload:
        if self.action is InstanceAction.CREATE and self.create is None:
            raise ValueError("create payload is required")
        if self.action is not InstanceAction.CREATE and not self.instance_provider_resource_id:
            raise ValueError("instance provider resource id is required")
        if self.action is not InstanceAction.REBOOT and self.reboot_type is not None:
            raise ValueError("reboot type is only valid for reboot")
        return self


class InstanceOperationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: InstanceAction
    instance: dict[str, Any]
    ports: list[dict[str, Any]] = Field(default_factory=list)
    volumes: list[dict[str, Any]] = Field(default_factory=list)
    provider_request_id: str | None = None


class InstanceReference(BaseModel):
    instance_id: UUID
    provider_resource_id: str
