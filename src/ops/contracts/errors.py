"""Pinned common error contract copied from CPS (OPS-local UTC helper)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def assert_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("occurred_at must be timezone-aware UTC")
    if value.utcoffset() != timedelta(0):
        raise ValueError("occurred_at must use UTC offset")
    return value


class ErrorCategory(StrEnum):
    VALIDATION = "VALIDATION"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    CAPABILITY = "CAPABILITY"
    AUTHENTICATION = "AUTHENTICATION"
    AUTHORIZATION = "AUTHORIZATION"
    QUOTA = "QUOTA"
    RATE_LIMIT = "RATE_LIMIT"
    TIMEOUT = "TIMEOUT"
    NETWORK = "NETWORK"
    PROVIDER = "PROVIDER"
    INTERNAL = "INTERNAL"


class CommonError(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str
    message: str
    category: ErrorCategory
    retryable: bool
    provider: str | None = None
    provider_service: str | None = None
    provider_request_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at_utc(cls, value: datetime) -> datetime:
        return assert_utc_datetime(value)


class DomainError(Exception):
    status_code = 500
    code = "INTERNAL_ERROR"
    category = ErrorCategory.INTERNAL
    retryable = False
    default_public_message = "Internal service error"

    def __init__(
        self,
        public_message: str | None = None,
        *,
        cause: object | None = None,
    ) -> None:
        self.public_message = public_message or type(self).default_public_message
        self.cause = cause
        super().__init__(self.public_message)


class ResourceNotFoundError(DomainError):
    status_code = 404
    code = "NOT_FOUND"
    category = ErrorCategory.NOT_FOUND
    default_public_message = "Resource not found"


class DomainConflictError(DomainError):
    status_code = 409
    code = "CONFLICT"
    category = ErrorCategory.CONFLICT
    default_public_message = "Conflict"


class CapabilityUnsupportedError(DomainError):
    status_code = 422
    code = "CAPABILITY_UNSUPPORTED"
    category = ErrorCategory.CAPABILITY
    default_public_message = "Capability unsupported"


class ProviderOperationError(DomainError):
    status_code = 502
    code = "PROVIDER_ERROR"
    category = ErrorCategory.PROVIDER
    default_public_message = "Provider operation failed"

    def __init__(self, cause: object | None = None) -> None:
        """Public message is fixed; diagnostic detail belongs only in ``cause``."""
        super().__init__(type(self).default_public_message, cause=cause)


class OperationTimeoutError(DomainError):
    status_code = 504
    code = "OPERATION_TIMEOUT"
    category = ErrorCategory.TIMEOUT
    retryable = True
    default_public_message = "Operation timed out"
