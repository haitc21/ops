"""Bounded HTTP resolver for CPS internal credential references."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import httpx

from ops.config import Settings
from ops.contracts.validation import CredentialResolution

MAX_RESOLUTION_BYTES = 16 * 1024


def _error_code(response: httpx.Response) -> str:
    """Retain a safe CPS error code without exposing the response body."""
    try:
        payload = response.json()
        error = payload.get("error") if isinstance(payload, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
    except (ValueError, TypeError):
        code = None
    if isinstance(code, str) and code and len(code) <= 128:
        return code
    return "CPS_UNAVAILABLE"


class CpsResolutionError(RuntimeError):
    """A normalized CPS resolution failure."""

    def __init__(self, code: str, *, retryable: bool) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class CredentialResolver:
    settings: Settings

    async def resolve(
        self, credential_reference: UUID, connection_id: UUID
    ) -> CredentialResolution:
        base_url = self.settings.require_cps_base_url.rstrip("/")
        url = f"{base_url}/internal/v1/credentials/{credential_reference}"
        timeout = httpx.Timeout(
            connect=5.0,
            read=float(self.settings.cps_timeout_seconds),
            write=5.0,
            pool=5.0,
        )
        response: httpx.Response | None = None
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                response = await client.get(
                    url, params={"provider_connection_id": str(connection_id)}
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise CpsResolutionError("CPS_UNAVAILABLE", retryable=True) from exc
        if len(response.content) > MAX_RESOLUTION_BYTES:
            raise CpsResolutionError("CPS_UNAVAILABLE", retryable=True)
        if response.status_code >= 500:
            raise CpsResolutionError(_error_code(response), retryable=True)
        if response.status_code in {404, 409}:
            raise CpsResolutionError("CREDENTIAL_REFERENCE_INVALID", retryable=False)
        if response.status_code != 200:
            raise CpsResolutionError("CPS_UNAVAILABLE", retryable=False)
        try:
            payload = response.json()
            if isinstance(payload, dict):
                payload.setdefault("schema_version", "1.0")
            return CredentialResolution.model_validate(payload)
        except (ValueError, TypeError) as exc:
            raise CpsResolutionError("CPS_UNAVAILABLE", retryable=True) from exc
        finally:
            response = None
