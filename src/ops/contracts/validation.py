"""Pinned consumer contracts for safe OpenStack connection validation."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ops.contracts.errors import CommonError
from ops.contracts.messages.envelope import MessageEnvelope

MAX_VALIDATION_DOCUMENT_BYTES = 64 * 1024
_FORBIDDEN = frozenset(
    {
        "password",
        "token",
        "authorization",
        "user_data",
        "private_key",
        "raw_catalog",
        "raw_response",
    }
)
_SERVICES = frozenset({"identity", "compute", "network", "image", "block_storage"})
_FEATURES = frozenset(
    {
        "connection.authenticate",
        "service.identity",
        "service.compute",
        "service.network",
        "service.image",
        "service.block_storage",
    }
)
_CATALOG_FEATURES = frozenset(
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


def _assert_safe(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if lowered in _FORBIDDEN or any(
                token in lowered for token in ("password", "token", "authorization", "private_key")
            ):
                raise ValueError(f"forbidden validation field: {key}")
            _assert_safe(child)
    elif isinstance(value, list):
        for child in value:
            _assert_safe(child)


class _Versioned(BaseModel):
    model_config = ConfigDict(extra="allow")
    schema_version: str
    sensitive: ClassVar[bool] = False

    @model_validator(mode="after")
    def validate_version_size(self) -> _Versioned:
        parts = self.schema_version.split(".")
        if len(parts) != 2 or not all(part.isdigit() for part in parts) or int(parts[0]) != 1:
            raise ValueError("unsupported major schema version")
        if not self.sensitive:
            _assert_safe(self.model_dump(mode="json"))
        if (
            len(json.dumps(self.model_dump(mode="json"), separators=(",", ":")).encode())
            > MAX_VALIDATION_DOCUMENT_BYTES
        ):
            raise ValueError("validation document exceeds maximum size")
        return self


class ServiceCapability(BaseModel):
    model_config = ConfigDict(extra="allow")
    available: bool
    min_version: str | None = None
    max_version: str | None = None
    reason: str | None = Field(default=None, max_length=256)


class FeatureCapability(BaseModel):
    model_config = ConfigDict(extra="allow")
    supported: bool
    reason: str | None = Field(default=None, max_length=256)


class CapabilityDocument(_Versioned):
    services: dict[str, ServiceCapability]
    features: dict[str, FeatureCapability]

    @model_validator(mode="after")
    def require_scoped_capabilities(self) -> CapabilityDocument:
        if not _SERVICES.issubset(self.services) or not _FEATURES.issubset(self.features):
            raise ValueError("capability document is missing required entries")
        minor = int(self.schema_version.split(".")[1])
        if minor >= 1 and not _CATALOG_FEATURES.issubset(self.features):
            raise ValueError("capability document is missing required catalog features")
        return self


class CredentialResolution(_Versioned):
    sensitive: ClassVar[bool] = True
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


class ValidationCompleted(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str = Field(pattern="^VALID$")
    capabilities: CapabilityDocument


def validate_validation_event(value: dict[str, Any]) -> dict[str, Any]:
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
