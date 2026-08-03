"""Pinned CPS↔OPS inventory batch contract."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_MAX_CATALOG_METADATA_BYTES = 64 * 1024
_MAX_CATALOG_METADATA_DEPTH = 4
_FORBIDDEN_METADATA_TOKENS = ("password", "token", "authorization", "private_key", "user_data")


class ImageVisibility(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    SHARED = "shared"
    COMMUNITY = "community"


def _validate_catalog_metadata(value: object, *, depth: int = 0) -> None:
    if depth > _MAX_CATALOG_METADATA_DEPTH:
        raise ValueError("catalog metadata exceeds maximum depth")
    if isinstance(value, dict):
        if len(value) > 128:
            raise ValueError("catalog metadata has too many entries")
        for key, child in value.items():
            if not isinstance(key, str) or not key or len(key) > 255:
                raise ValueError("catalog metadata key is invalid")
            if any(token in key.lower() for token in _FORBIDDEN_METADATA_TOKENS):
                raise ValueError(f"catalog metadata contains forbidden key: {key}")
            _validate_catalog_metadata(child, depth=depth + 1)
    elif isinstance(value, list):
        if len(value) > 128:
            raise ValueError("catalog metadata has too many entries")
        for child in value:
            _validate_catalog_metadata(child, depth=depth + 1)
    elif isinstance(value, str):
        if len(value) > 4096:
            raise ValueError("catalog metadata string is too long")
    elif value is not None and not isinstance(value, bool | int | float):
        raise ValueError("catalog metadata value is invalid")


class InventoryResourceType(StrEnum):
    REGION = "region"
    DOMAIN = "domain"
    PROJECT = "project"
    FLAVOR = "flavor"
    AVAILABILITY_ZONE = "availability-zone"
    IMAGE = "image"
    INSTANCE = "instance"
    NETWORK = "network"
    SUBNET = "subnet"
    PORT = "port"
    ROUTER = "router"
    ROUTER_INTERFACE = "router_interface"
    SECURITY_GROUP = "security_group"
    SECURITY_GROUP_RULE = "security_group_rule"
    FLOATING_IP = "floating_ip"
    VOLUME = "volume"
    VOLUME_TYPE = "volume-type"
    SNAPSHOT = "volume-snapshot"
    KEYPAIR = "keypair"


class InventoryCollectionStatus(StrEnum):
    COMPLETE = "COMPLETE"
    SKIPPED_UNSUPPORTED = "SKIPPED_UNSUPPORTED"


class InventoryBatchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_resource_id: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    provider_status: str | None = Field(default=None, max_length=64)
    provider_created_at: str | None = None
    provider_updated_at: str | None = None
    lifecycle_state: str = Field(default="ACTIVE", pattern="^(ACTIVE|DELETED)$")
    project_provider_resource_id: str | None = Field(default=None, max_length=255)
    volume_type_provider_resource_id: str | None = Field(default=None, max_length=255)
    size_gib: int | None = Field(default=None, ge=1, le=16384)
    bootable: bool | None = None
    root: bool | None = None
    encrypted: bool | None = None
    metadata: dict[str, Any] | None = None
    availability_zone: str | None = Field(default=None, max_length=255)
    attachments: list[dict[str, Any]] = Field(default_factory=list, max_length=32)
    vcpus: int | None = Field(default=None, ge=0, le=4096)
    ram_mib: int | None = Field(default=None, ge=0, le=16_777_216)
    root_disk_gib: int | None = Field(default=None, ge=0, le=1_048_576)
    ephemeral_disk_gib: int | None = Field(default=None, ge=0, le=1_048_576)
    swap_mib: int | None = Field(default=None, ge=0, le=16_777_216)
    is_public: bool | None = None
    enabled: bool | None = None
    visibility: ImageVisibility | None = None
    is_protected: bool | None = None
    container_format: str | None = Field(default=None, min_length=1, max_length=32)
    disk_format: str | None = Field(default=None, min_length=1, max_length=32)
    size_bytes: int | None = Field(default=None, ge=0, le=9_223_372_036_854_775_807)
    virtual_size_bytes: int | None = Field(default=None, ge=0, le=9_223_372_036_854_775_807)
    tags: list[Annotated[str, Field(min_length=1, max_length=255)]] | None = Field(
        default=None, max_length=64
    )
    properties: dict[str, Any] | None = None
    checksum: str | None = Field(default=None, max_length=128)
    min_disk_gib: int | None = Field(default=None, ge=0, le=1_048_576)
    min_ram_mib: int | None = Field(default=None, ge=0, le=16_777_216)
    catalog_approved: bool | None = None
    extra_specs: dict[str, Any] | None = None
    access_project_ids: list[Annotated[str, Field(min_length=1, max_length=255)]] | None = Field(
        default=None, max_length=256
    )
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("disk_format", "container_format")
    @classmethod
    def validate_catalog_format(cls, value: str | None) -> str | None:
        if value is not None and value != value.lower():
            raise ValueError("catalog format must be lowercase")
        return value

    @field_validator("properties", "extra_specs")
    @classmethod
    def validate_catalog_metadata(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return value
        _validate_catalog_metadata(value)
        serialized = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        if len(serialized.encode("utf-8")) > _MAX_CATALOG_METADATA_BYTES:
            raise ValueError("catalog metadata exceeds maximum size")
        return value

    @field_validator("access_project_ids")
    @classmethod
    def validate_access_project_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and value != sorted(set(value)):
            raise ValueError("access project IDs must be sorted and unique")
        return value


def compute_inventory_checksum(items: list[dict[str, Any]]) -> str:
    canonical = json.dumps(items, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class InventoryBatchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sync_id: UUID
    resource_type: InventoryResourceType
    sequence: int = Field(ge=1)
    is_last: bool
    collection_status: InventoryCollectionStatus = InventoryCollectionStatus.COMPLETE
    item_count: int = Field(ge=0)
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    items: list[InventoryBatchItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_integrity(self) -> InventoryBatchPayload:
        if self.item_count != len(self.items):
            raise ValueError("item_count does not match items")
        item_dicts = [
            item.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
            for item in self.items
        ]
        if self.checksum != compute_inventory_checksum(item_dicts):
            raise ValueError("checksum does not match items")
        if self.collection_status is InventoryCollectionStatus.SKIPPED_UNSUPPORTED and self.items:
            raise ValueError("unsupported collection must not contain items")
        if (
            self.collection_status is InventoryCollectionStatus.SKIPPED_UNSUPPORTED
            and not self.is_last
        ):
            raise ValueError("unsupported collection must close with is_last")
        return self
