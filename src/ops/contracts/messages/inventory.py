"""Canonical CPS↔OPS inventory batch contract."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator
from pydantic_core import PydanticCustomError

from ops.contracts.messages.envelope import parse_schema_version
from ops.contracts.safe_metadata import (
    MAX_ATTACHMENT_SERIALIZED_BYTES,
    MAX_ROOT_MAP_ENTRIES,
    is_secret_key,
    validate_attachment_tree,
    validate_disk_format,
    validate_metadata_tree,
    validate_provider_timestamp,
    validate_safe_catalog_string,
    validate_safe_project_id,
    validate_serialized_size,
)

_MAX_PROVIDER_STATUS_LENGTH = 64
_MAX_CHECKSUM_LENGTH = 128
_CATALOG_TOP_LEVEL_STRING_FIELDS: tuple[tuple[str, int], ...] = (
    ("provider_resource_id", 255),
    ("name", 255),
    ("provider_status", _MAX_PROVIDER_STATUS_LENGTH),
    ("project_provider_resource_id", 255),
    ("volume_type_provider_resource_id", 255),
    ("volume_provider_resource_id", 255),
    ("availability_zone", 255),
    ("checksum", _MAX_CHECKSUM_LENGTH),
)
_MAX_TAGS = 64
_MAX_TAG_LENGTH = 255
_MAX_ACCESS_PROJECT_IDS = 256
_MAX_ACCESS_PROJECT_ID_LENGTH = 255
_MAX_CONTAINER_FORMAT_LENGTH = 255
_MAX_ATTACHMENTS = 32
_IMAGE_VISIBILITY = frozenset({"public", "private", "shared", "community"})
_OWNER_FIELD_KEYS = ("project_provider_resource_id", "project_id", "tenant_id")
OWNERSHIP_CONFLICT_MESSAGE = "conflicting ownership sources"


class OwnershipConflictError(ValueError):
    """Stable ownership conflict that must not embed raw owner identifiers."""

    def __str__(self) -> str:
        return OWNERSHIP_CONFLICT_MESSAGE


# Backward-compatible alias for catalog projection imports.
_validate_metadata_tree = validate_metadata_tree


class InventoryResourceType(StrEnum):
    DOMAIN = "domain"
    REGION = "region"
    PROJECT = "project"
    FLAVOR = "flavor"
    AVAILABILITY_ZONE = "availability-zone"
    IMAGE = "image"
    INSTANCE = "instance"
    NETWORK = "network"
    SUBNET = "subnet"
    PORT = "port"
    VOLUME = "volume"
    VOLUME_TYPE = "volume-type"
    VOLUME_SNAPSHOT = "volume-snapshot"
    KEYPAIR = "keypair"
    ROLE_ASSIGNMENT = "role-assignment"
    QUOTA = "quota"


class InventoryCollectionStatus(StrEnum):
    COMPLETE = "COMPLETE"
    SKIPPED_UNSUPPORTED = "SKIPPED_UNSUPPORTED"


def _strict_bool(value: object, label: str) -> None:
    if type(value) is not bool:
        raise ValueError(f"{label} must be boolean")


def _strict_non_negative_int(value: object, label: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")


def resolve_owner_project_provider_resource_id(
    item: dict[str, Any],
    attrs: dict[str, Any] | None = None,
) -> str | None:
    """Return the single owner project id or raise when sources disagree."""
    metadata = attrs or {}
    seen: dict[str, str] = {}
    for container, label in ((item, "item"), (metadata, "attributes")):
        for key in _OWNER_FIELD_KEYS:
            if key not in container:
                continue
            value = container[key]
            if value is None or value == "":
                continue
            validated = validate_safe_project_id(value, label=f"{label}.{key}")
            seen[f"{label}.{key}"] = validated
    distinct = set(seen.values())
    if len(distinct) > 1:
        raise OwnershipConflictError
    for container in (item, metadata):
        for key in _OWNER_FIELD_KEYS:
            value = container.get(key)
            if value is not None and value != "":
                return validate_safe_project_id(value, label=f"{key}")
    return None


def _validate_attachments(attachments: object) -> list[dict[str, Any]]:
    if not isinstance(attachments, list):
        raise ValueError("attachments must be an array")
    if len(attachments) > _MAX_ATTACHMENTS:
        raise ValueError("attachments exceed maximum length")
    bounded: list[dict[str, Any]] = []
    for attachment in attachments:
        if not isinstance(attachment, dict):
            raise ValueError("attachment must be an object")
        validate_attachment_tree(attachment)
        bounded.append(attachment)
    serialized = json.dumps(bounded, separators=(",", ":"), sort_keys=True).encode()
    if len(serialized) > MAX_ATTACHMENT_SERIALIZED_BYTES:
        raise ValueError("attachments exceed maximum serialized size")
    return bounded


def _validate_catalog_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    if len(attributes) > MAX_ROOT_MAP_ENTRIES:
        raise ValueError("attributes exceed maximum entries")
    for key in attributes:
        if is_secret_key(str(key)):
            raise ValueError(f"forbidden inventory attribute key: {key}")
    if "catalog_approved" in attributes:
        _strict_bool(attributes["catalog_approved"], "catalog_approved")
    if "is_protected" in attributes:
        _strict_bool(attributes["is_protected"], "is_protected")
    if "container_format" in attributes:
        validate_safe_catalog_string(
            attributes["container_format"],
            label="container_format",
            max_length=_MAX_CONTAINER_FORMAT_LENGTH,
        )
    if "virtual_size_bytes" in attributes:
        _strict_non_negative_int(
            attributes["virtual_size_bytes"],
            "virtual_size_bytes",
        )
    if "tags" in attributes:
        tags = attributes["tags"]
        if not isinstance(tags, list) or len(tags) > _MAX_TAGS:
            raise ValueError("tags exceed maximum length")
        for tag in tags:
            validate_safe_catalog_string(tag, label="tag value", max_length=_MAX_TAG_LENGTH)
    for map_key in ("properties", "extra_specs"):
        if map_key in attributes:
            map_value = attributes[map_key]
            if not isinstance(map_value, dict):
                raise ValueError(f"{map_key} must be an object")
            validate_metadata_tree(map_value)
    for extra_key, extra_value in attributes.items():
        if extra_key in {
            "catalog_approved",
            "is_protected",
            "container_format",
            "virtual_size_bytes",
            "tags",
            "properties",
            "extra_specs",
            "access_project_ids",
            "member_project_ids",
        }:
            continue
        validate_metadata_tree({extra_key: extra_value})
    if "access_project_ids" in attributes:
        access_ids = attributes["access_project_ids"]
        if not isinstance(access_ids, list) or len(access_ids) > _MAX_ACCESS_PROJECT_IDS:
            raise ValueError("access_project_ids exceed maximum length")
        normalized: list[str] = []
        for project_id in access_ids:
            validate_safe_project_id(project_id, label="access_project_ids entry")
            normalized.append(project_id)
        attributes["access_project_ids"] = sorted(set(normalized))
    if "member_project_ids" in attributes:
        member_ids = attributes["member_project_ids"]
        if not isinstance(member_ids, list) or len(member_ids) > _MAX_ACCESS_PROJECT_IDS:
            raise ValueError("member_project_ids exceed maximum length")
        for project_id in member_ids:
            validate_safe_project_id(project_id, label="member_project_ids entry")
    validate_serialized_size(attributes, label="inventory attributes")
    return attributes


class InventoryBatchItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

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
    volume_provider_resource_id: str | None = Field(default=None, max_length=255)
    snapshot_size_gib: int | None = Field(default=None, ge=1, le=16384)
    visibility: Literal["public", "private", "shared", "community"] | None = None
    size_bytes: int | None = Field(default=None, ge=0, le=9_223_372_036_854_775_807)
    min_disk_gib: int | None = Field(default=None, ge=0, le=1_048_576)
    min_ram_mib: int | None = Field(default=None, ge=0, le=16_777_216)
    disk_format: str | None = Field(default=None, min_length=1, max_length=32)
    checksum: str | None = Field(default=None, max_length=128)
    vcpus: int | None = Field(default=None, ge=0, le=4096)
    ram_mib: int | None = Field(default=None, ge=0, le=16_777_216)
    root_disk_gib: int | None = Field(default=None, ge=0, le=1_048_576)
    ephemeral_disk_gib: int | None = Field(default=None, ge=0, le=1_048_576)
    swap_mib: int | None = Field(default=None, ge=0, le=16_777_216)
    is_public: bool | None = None
    enabled: bool | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("disk_format")
    @classmethod
    def validate_disk_format_field(cls, value: str | None) -> str | None:
        return validate_disk_format(value)

    @model_validator(mode="after")
    def validate_top_level_catalog_strings(self) -> InventoryBatchItem:
        for field_name, max_length in _CATALOG_TOP_LEVEL_STRING_FIELDS:
            value = getattr(self, field_name)
            if value is None:
                continue
            validate_safe_catalog_string(value, label=field_name, max_length=max_length)
        for field_name in ("provider_created_at", "provider_updated_at"):
            value = getattr(self, field_name)
            if value is None:
                continue
            validate_provider_timestamp(value, label=field_name)
        return self

    @model_validator(mode="before")
    @classmethod
    def validate_attacker_controlled_metadata(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        metadata = data.get("metadata")
        if metadata is not None:
            validate_metadata_tree(metadata)
            validate_serialized_size(metadata, label="metadata")
        attachments = data.get("attachments")
        if attachments is not None:
            data["attachments"] = _validate_attachments(attachments)
        return data

    @model_validator(mode="after")
    def validate_ownership_sources(self) -> InventoryBatchItem:
        try:
            resolve_owner_project_provider_resource_id(
                self.model_dump(mode="python"),
                dict(self.attributes),
            )
        except OwnershipConflictError as exc:
            raise PydanticCustomError("ownership_conflict", str(exc)) from exc
        return self

    @model_validator(mode="after")
    def validate_catalog_fields(self) -> InventoryBatchItem:
        if self.visibility is not None and self.visibility not in _IMAGE_VISIBILITY:
            raise ValueError("visibility is invalid")
        self.attributes = _validate_catalog_attributes(dict(self.attributes))
        return self


def _hash_inventory_item_dicts(dicts: list[dict[str, Any]]) -> str:
    payload = json.dumps(dicts, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def inventory_item_dicts_v1_0(items: list[InventoryBatchItem]) -> list[dict[str, Any]]:
    return [
        item.model_dump(mode="json", exclude_none=True, exclude_defaults=True) for item in items
    ]


def inventory_item_dicts_v1_1(items: list[InventoryBatchItem]) -> list[dict[str, Any]]:
    return canonical_inventory_item_dicts([item.model_dump(mode="json") for item in items])


def compute_inventory_checksum_v1_0(items: list[InventoryBatchItem]) -> str:
    """Legacy schema 1.0 checksum emitted by older OPS producers."""
    return _hash_inventory_item_dicts(inventory_item_dicts_v1_0(items))


def compute_inventory_checksum_v1_1(items: list[InventoryBatchItem]) -> str:
    """Canonical schema 1.1 checksum after full safety validation."""
    return _hash_inventory_item_dicts(inventory_item_dicts_v1_1(items))


def canonical_inventory_item_dicts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        InventoryBatchItem.model_validate(item).model_dump(
            mode="json",
            exclude_none=True,
            exclude_defaults=True,
        )
        for item in items
    ]


def canonicalize_inventory_item(item: dict[str, Any]) -> dict[str, Any]:
    """Validate and sanitize one inventory item before repository persistence."""
    validated = InventoryBatchItem.model_validate(item)
    return validated.model_dump(mode="json", exclude_none=True)


def compute_inventory_checksum(items: list[dict[str, Any]]) -> str:
    """Canonical v1.1 checksum helper for producers and contract tests."""
    return _hash_inventory_item_dicts(canonical_inventory_item_dicts(items))


_SCHEMA_1_1_TOP_LEVEL_FIELDS = frozenset(
    {
        "visibility",
        "size_bytes",
        "min_disk_gib",
        "min_ram_mib",
        "disk_format",
        "checksum",
        "vcpus",
        "ram_mib",
        "root_disk_gib",
        "ephemeral_disk_gib",
        "swap_mib",
        "is_public",
        "enabled",
        "provider_created_at",
        "provider_updated_at",
    }
)
_SCHEMA_1_1_ATTRIBUTE_KEYS = frozenset(
    {
        "catalog_approved",
        "is_protected",
        "container_format",
        "virtual_size_bytes",
        "tags",
        "properties",
        "extra_specs",
        "access_project_ids",
        "member_project_ids",
    }
)


def _reject_schema_1_0_catalog_fields(items: list[InventoryBatchItem]) -> None:
    for item in items:
        payload = item.model_dump(mode="python")
        for field_name in _SCHEMA_1_1_TOP_LEVEL_FIELDS:
            if payload.get(field_name) is not None:
                raise ValueError("schema version 1.0 does not allow catalog enrichment fields")
        attributes = payload.get("attributes") or {}
        for attribute_key in attributes:
            if attribute_key in _SCHEMA_1_1_ATTRIBUTE_KEYS:
                raise ValueError("schema version 1.0 does not allow catalog enrichment fields")


def _verify_inventory_batch_checksum(
    *,
    items: list[InventoryBatchItem],
    checksum: str,
    schema_version: str,
) -> None:
    major, minor = parse_schema_version(schema_version)
    if major == 1 and minor == 0:
        _reject_schema_1_0_catalog_fields(items)
        expected = compute_inventory_checksum_v1_0(items)
    else:
        expected = compute_inventory_checksum_v1_1(items)
    if checksum != expected:
        raise ValueError("checksum does not match items")


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
    def validate_integrity(self, info: ValidationInfo) -> InventoryBatchPayload:
        if self.item_count != len(self.items):
            raise ValueError("item_count does not match items")
        schema_version = str((info.context or {}).get("schema_version", "1.1"))
        _verify_inventory_batch_checksum(
            items=self.items,
            checksum=self.checksum,
            schema_version=schema_version,
        )
        if self.collection_status is InventoryCollectionStatus.SKIPPED_UNSUPPORTED and self.items:
            raise ValueError("unsupported collection must not contain items")
        if (
            self.collection_status is InventoryCollectionStatus.SKIPPED_UNSUPPORTED
            and not self.is_last
        ):
            raise ValueError("unsupported collection must close with is_last")
        return self
