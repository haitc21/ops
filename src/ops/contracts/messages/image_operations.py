"""Pinned CPS↔OPS safe Glance image lifecycle command contract."""

from __future__ import annotations

import ipaddress
import os
from enum import StrEnum
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ops.contracts.messages.resource_operations import ScopeKind

_SAFE_DISK_FORMATS = {"aki", "ami", "ari", "iso", "qcow2", "raw", "vdi", "vhd", "vhdx", "vmdk"}
_SAFE_CONTAINER_FORMATS = {"ami", "ari", "aki", "bare", "ova"}
_SECRET_MARKERS = (
    "password",
    "token",
    "secret",
    "authorization",
    "private_key",
    "user_data",
    "bytes",
    "base64",
)
# Deployment-owned policy; it is intentionally never accepted from a request.
IMAGE_IMPORT_ALLOWED_HOSTS = frozenset(
    host.strip().lower().rstrip(".")
    for host in os.getenv("OPS_IMAGE_IMPORT_ALLOWED_HOSTS", "images.example.test").split(",")
    if host.strip()
)


class ImageOperation(StrEnum):
    CREATE = "create"
    IMPORT_URL = "import_url"
    PATCH_METADATA = "patch_metadata"
    SET_VISIBILITY = "set_visibility"
    SET_PROTECTION = "set_protection"
    GRANT_MEMBER = "grant_member"
    REVOKE_MEMBER = "revoke_member"
    DEACTIVATE = "deactivate"
    REACTIVATE = "reactivate"
    DELETE = "delete"


class ImageOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = Field(default="1.0", pattern=r"^1\.0$")
    operation_id: UUID
    resource_type: str = Field(default="image", pattern=r"^image$")
    operation: ImageOperation
    required_scope: ScopeKind = ScopeKind.SYSTEM
    provider_connection_id: UUID
    provider_resource_id: str | None = Field(default=None, min_length=1, max_length=255)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1024)
    disk_format: str | None = Field(default=None, max_length=16)
    container_format: str = Field(default="bare", max_length=16)
    architecture: str | None = Field(default=None, max_length=64)
    kernel_id: str | None = Field(default=None, max_length=255)
    ramdisk_id: str | None = Field(default=None, max_length=255)
    min_disk_gib: int = Field(default=0, ge=0, le=65536)
    min_ram_mib: int = Field(default=0, ge=0, le=1048576)
    visibility: str | None = Field(default=None, pattern=r"^(private|shared|community|public)$")
    protected: bool | None = None
    tags: list[str] = Field(default_factory=list, max_length=50)
    metadata: dict[str, str] = Field(default_factory=dict, max_length=50)
    remove_metadata_keys: list[str] = Field(default_factory=list, max_length=50)
    member_project_id: str | None = Field(default=None, min_length=1, max_length=255)
    source_url: str | None = Field(default=None, max_length=2048)
    operation_marker: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("disk_format")
    @classmethod
    def validate_disk_format(cls, value: str | None) -> str | None:
        if value is not None and value.lower() not in _SAFE_DISK_FORMATS:
            raise ValueError("disk_format is not launchable")
        return value.lower() if value else value

    @field_validator("container_format")
    @classmethod
    def validate_container_format(cls, value: str) -> str:
        if value.lower() not in _SAFE_CONTAINER_FORMATS:
            raise ValueError("container_format is not supported")
        return value.lower()

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value) or any(not item or len(item) > 128 for item in value):
            raise ValueError("tags must be unique and bounded")
        return value

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, str]) -> dict[str, str]:
        for key, item in value.items():
            if not key or len(key) > 128 or len(item) > 1024:
                raise ValueError("metadata keys and values must be bounded")
            if any(marker in key.lower() or marker in item.lower() for marker in _SECRET_MARKERS):
                raise ValueError("secret-like metadata is not accepted")
        return value

    @field_validator("remove_metadata_keys")
    @classmethod
    def validate_remove_keys(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value) or any(not item or len(item) > 128 for item in value):
            raise ValueError("metadata removal keys must be unique and bounded")
        return value

    @model_validator(mode="after")
    def validate_lifecycle(self) -> ImageOperationRequest:
        if set(self.metadata).intersection(self.remove_metadata_keys):
            raise ValueError("metadata keys cannot be updated and removed together")
        if self.operation in {ImageOperation.CREATE, ImageOperation.IMPORT_URL} and (
            not self.name or not self.disk_format
        ):
            raise ValueError("image create/import requires name and disk_format")
        if self.operation is ImageOperation.IMPORT_URL:
            self._validate_source_url()
        elif self.source_url is not None:
            raise ValueError("source URL fields are only valid for import_url")
        if (
            self.operation not in {ImageOperation.CREATE, ImageOperation.IMPORT_URL}
            and not self.provider_resource_id
        ):
            raise ValueError("image lifecycle operation requires provider_resource_id")
        if (
            self.operation in {ImageOperation.GRANT_MEMBER, ImageOperation.REVOKE_MEMBER}
            and not self.member_project_id
        ):
            raise ValueError("member operation requires member_project_id")
        if (
            self.operation not in {ImageOperation.GRANT_MEMBER, ImageOperation.REVOKE_MEMBER}
            and self.member_project_id
        ):
            raise ValueError("member_project_id only applies to member operations")
        return self

    def _validate_source_url(self) -> None:
        if not self.source_url:
            raise ValueError("import_url requires source_url")
        parsed = urlsplit(self.source_url)
        if (
            parsed.scheme.lower() != "https"
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("source URL must be allowlisted HTTPS without credentials or query")
        host = parsed.hostname
        if not host or host.lower().rstrip(".") not in IMAGE_IMPORT_ALLOWED_HOSTS:
            raise ValueError("source URL host is not allowlisted")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if (
                host.lower() in {"localhost", "localhost.localdomain"}
                or "base64" in parsed.path.lower()
            ):
                raise ValueError("source URL target is not allowed") from None
        else:
            raise ValueError("IP literal source URL is not allowed")
