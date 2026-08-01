"""Contracts for safe OpenStack connection validation messages."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ops.contracts.errors import CommonError
from ops.contracts.messages.envelope import MessageEnvelope
from ops.contracts.safe_metadata import (
    MAX_CAPABILITY_REASON_STRING_LENGTH,
    MAX_CAPABILITY_SCHEMA_VERSION_LENGTH,
    MAX_CAPABILITY_VERSION_STRING_LENGTH,
    MAX_ROOT_MAP_ENTRIES,
    is_secret_value,
    validate_capability_extra_tree,
    validate_serialized_size,
)

_REQUIRED_SERVICES = frozenset({"identity", "compute", "network", "image", "block_storage"})
_CATALOG_CAPABILITY_KEYS = frozenset(
    {
        "image.import",
        "image.member",
        "image.deactivate",
        "image.reactivate",
        "flavor.create",
        "flavor.delete",
        "flavor.access",
        "flavor.extra_specs",
    }
)
_REQUIRED_FEATURES = frozenset(
    {
        "connection.authenticate",
        "service.identity",
        "service.compute",
        "service.network",
        "service.image",
        "service.block_storage",
    }
)


class _VersionedContract(BaseModel):
    model_config = ConfigDict(extra="allow")
    schema_version: str = Field(max_length=MAX_CAPABILITY_SCHEMA_VERSION_LENGTH)
    supported_major: ClassVar[int] = 1
    allow_sensitive_fields: ClassVar[bool] = False

    @model_validator(mode="after")
    def validate_version_and_size(self) -> _VersionedContract:
        parts = self.schema_version.split(".")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            raise ValueError("invalid schema version")
        if int(parts[0]) != self.supported_major:
            raise ValueError("unsupported major schema version")
        if not self.allow_sensitive_fields:
            payload = self.model_dump(mode="json")
            validate_capability_extra_tree(payload)
            validate_serialized_size(payload, label="validation document")
        return self


class ServiceCapability(BaseModel):
    model_config = ConfigDict(extra="allow")
    available: bool
    min_version: str | None = None
    max_version: str | None = None
    reason: str | None = Field(default=None, max_length=256)

    @field_validator("available", mode="before")
    @classmethod
    def validate_available_scalar(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("available must be boolean")
        return value

    @model_validator(mode="after")
    def validate_bounded_strings(self) -> ServiceCapability:
        for label, value, max_length in (
            ("min_version", self.min_version, MAX_CAPABILITY_VERSION_STRING_LENGTH),
            ("max_version", self.max_version, MAX_CAPABILITY_VERSION_STRING_LENGTH),
            ("reason", self.reason, MAX_CAPABILITY_REASON_STRING_LENGTH),
        ):
            if value is None:
                continue
            if type(value) is not str:
                raise ValueError(f"{label} must be a string")
            if len(value) > max_length:
                raise ValueError(f"{label} exceeds maximum length")
            if is_secret_value(value):
                raise ValueError(f"forbidden secret-bearing {label}")
        return self


class FeatureCapability(BaseModel):
    model_config = ConfigDict(extra="allow")
    supported: bool
    reason: str | None = Field(default=None, max_length=256)

    @field_validator("supported", mode="before")
    @classmethod
    def validate_supported_scalar(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("supported must be boolean")
        return value

    @model_validator(mode="after")
    def validate_bounded_strings(self) -> FeatureCapability:
        if self.reason is None:
            return self
        if type(self.reason) is not str:
            raise ValueError("reason must be a string")
        if len(self.reason) > MAX_CAPABILITY_REASON_STRING_LENGTH:
            raise ValueError("reason exceeds maximum length")
        if is_secret_value(self.reason):
            raise ValueError("forbidden secret-bearing reason")
        return self


class CapabilityDocument(_VersionedContract):
    """Provider-neutral, bounded, secret-free capability result."""

    services: dict[str, ServiceCapability]
    features: dict[str, FeatureCapability]

    @model_validator(mode="after")
    def validate_version_and_size(self) -> CapabilityDocument:
        if len(self.schema_version) > MAX_CAPABILITY_SCHEMA_VERSION_LENGTH:
            raise ValueError("schema_version exceeds maximum length")
        parts = self.schema_version.split(".")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            raise ValueError("invalid schema version")
        if int(parts[0]) != self.supported_major:
            raise ValueError("unsupported major schema version")
        if len(self.services) > MAX_ROOT_MAP_ENTRIES:
            raise ValueError("services exceed maximum entries")
        if len(self.features) > MAX_ROOT_MAP_ENTRIES:
            raise ValueError("features exceed maximum entries")
        payload = self.model_dump(mode="json")
        validate_capability_extra_tree(payload)
        validate_serialized_size(payload, label="validation document")
        return self

    @model_validator(mode="after")
    def validate_required_capabilities(self) -> CapabilityDocument:
        if not _REQUIRED_SERVICES.issubset(self.services):
            raise ValueError("capability document is missing required services")
        if not _REQUIRED_FEATURES.issubset(self.features):
            raise ValueError("capability document is missing required features")
        _major, minor = self.schema_version.split(".")
        if int(minor) >= 1 and not _CATALOG_CAPABILITY_KEYS.issubset(self.features):
            raise ValueError("capability document is missing catalog administration features")
        return self


class CredentialResolution(_VersionedContract):
    """Internal-only cleartext resolution; never accepted as an event payload."""

    allow_sensitive_fields: ClassVar[bool] = True

    auth_url: str = Field(min_length=1, max_length=2048)
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=4096)
    user_domain_name: str = Field(min_length=1, max_length=255)
    scope_kind: str = Field(default="PROJECT", pattern="^(SYSTEM|DOMAIN|PROJECT)$")
    project_name: str = Field(min_length=1, max_length=255)
    project_domain_name: str = Field(min_length=1, max_length=255)
    region_name: str = Field(min_length=1, max_length=255)
    interface: str = Field(pattern="^(public|internal|admin)$")
    verify_tls: bool
    ca_cert_pem: str | None = Field(default=None, max_length=32768)


class ValidationProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")
    progress: int = Field(ge=0, le=100)
    state: str = Field(pattern="^(RUNNING|WAITING_PROVIDER)$")
    message: str = Field(min_length=1, max_length=256)
    provider_resource_id: str | None = Field(default=None, max_length=255)
    provider_status: str | None = Field(default=None, max_length=64)


class ValidationCompleted(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str = Field(pattern="^VALID$")
    capabilities: CapabilityDocument


def validate_validation_event(value: dict[str, Any]) -> dict[str, Any]:
    """Validate an event envelope and its allow-listed validation payload."""
    if "credential_reference" in value:
        raise ValueError("validation events must omit credential_reference")
    envelope = MessageEnvelope.model_validate(value)
    if envelope.message_type.endswith(".progress"):
        ValidationProgress.model_validate(envelope.payload)
    elif envelope.message_type.endswith(".completed"):
        result = envelope.payload.get("result")
        if not isinstance(result, dict):
            raise ValueError("completed validation result is invalid")
        ValidationCompleted.model_validate(result)
    elif envelope.message_type.endswith(".failed"):
        error = envelope.payload.get("error")
        if not isinstance(error, dict):
            raise ValueError("failed validation error is invalid")
        CommonError.model_validate(error)
    else:
        raise ValueError("unsupported validation event type")
    return value
