"""Pinned CPS↔OPS inventory batch contract."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InventoryResourceType(StrEnum):
    REGION = "region"
    DOMAIN = "domain"
    PROJECT = "project"
    FLAVOR = "flavor"
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
    attributes: dict[str, Any] = Field(default_factory=dict)


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
