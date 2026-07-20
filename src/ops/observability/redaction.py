"""Secret redaction helpers for logs and structured payloads."""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

_SECRET_KEYS = {
    "password",
    "token",
    "authorization",
    "user_data",
    "secret",
    "api_key",
    "private_key",
    "ca_private_key",
    "access_key",
}

_AUTH_HEADER_RE = re.compile(r"(?i)(authorization\s*[:=]\s*)(Bearer\s+\S+|\S+)")
_PASSWORD_ASSIGN_RE = re.compile(r'(?i)(password\s*[:=]\s*)("[^"]*"|\'[^\']*\'|[^\s,"]+)')
_TOKEN_ASSIGN_RE = re.compile(r'(?i)(token\s*[:=]\s*)("[^"]*"|\'[^\']*\'|[^\s,"]+)')
_USER_DATA_ASSIGN_RE = re.compile(r'(?i)(user_data\s*[:=]\s*)("[^"]*"|\'[^\']*\'|[^\s,"]+)')


def _is_secret_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    return (
        normalized in _SECRET_KEYS
        or normalized.endswith("_password")
        or normalized.endswith("_token")
        or normalized.endswith("_private_key")
    )


def redact_mapping(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and _is_secret_key(key):
                redacted[key] = REDACTED
            else:
                redacted[key] = redact_mapping(item)
        return redacted
    if isinstance(value, list):
        return [redact_mapping(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_mapping(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_text(text: str) -> str:
    redacted = _AUTH_HEADER_RE.sub(rf"\1{REDACTED}", text)
    redacted = _PASSWORD_ASSIGN_RE.sub(rf"\1{REDACTED}", redacted)
    redacted = _TOKEN_ASSIGN_RE.sub(rf"\1{REDACTED}", redacted)
    redacted = _USER_DATA_ASSIGN_RE.sub(rf"\1{REDACTED}", redacted)
    return redacted
