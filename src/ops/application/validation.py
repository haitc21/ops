"""Command envelope validation for OPS application dispatch."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from ops.contracts.messages.envelope import MessageEnvelope

EnvelopeRejectCode = Literal["invalid_envelope", "unsupported_major"]


@dataclass(frozen=True, slots=True)
class EnvelopeReject(Exception):
    code: EnvelopeRejectCode

    def __str__(self) -> str:
        return self.code


def validate_command_envelope(data: Any) -> MessageEnvelope:
    """Validate a parsed JSON value as a supported command envelope."""
    payload = _normalize_payload(data)
    try:
        return MessageEnvelope.model_validate(payload)
    except ValidationError as exc:
        if _is_unsupported_major_error(exc):
            raise EnvelopeReject("unsupported_major") from None
        raise EnvelopeReject("invalid_envelope") from None
    except ValueError as exc:
        if "unsupported major" in str(exc):
            raise EnvelopeReject("unsupported_major") from None
        raise EnvelopeReject("invalid_envelope") from None


def _normalize_payload(data: Any) -> Any:
    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise EnvelopeReject("invalid_envelope") from exc
        return _normalize_payload(text)
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError as exc:
            raise EnvelopeReject("invalid_envelope") from exc
        return _normalize_payload(parsed)
    if isinstance(data, dict):
        return data
    raise EnvelopeReject("invalid_envelope")


def _is_unsupported_major_error(exc: ValidationError) -> bool:
    return any(
        error.get("type") == "value_error" and "unsupported major" in str(error.get("msg", ""))
        for error in exc.errors()
    )
