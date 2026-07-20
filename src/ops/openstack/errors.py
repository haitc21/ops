"""Normalize OpenStackSDK exceptions into the pinned common error contract."""

from __future__ import annotations

import keystoneauth1.exceptions.connection as ks_exc
import requests.exceptions as req_exc
from openstack import exceptions as os_exc

from ops.contracts.errors import CommonError, ErrorCategory

_PUBLIC_MESSAGE = "OpenStack provider request failed"
_ALLOWLISTED_REQUEST_ID_HEADERS = ("x-openstack-request-id", "x-request-id")


def _http_status(exc: BaseException) -> int | None:
    for attr in ("http_status", "status_code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    if response is not None:
        response_status = getattr(response, "status_code", None)
        if isinstance(response_status, int):
            return response_status
    return None


def _request_id_from_response_headers(response: object) -> str | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    for header_name in _ALLOWLISTED_REQUEST_ID_HEADERS:
        value = headers.get(header_name)
        if isinstance(value, str) and value:
            return value
    return None


def _request_id(exc: BaseException) -> str | None:
    direct = getattr(exc, "request_id", None)
    if isinstance(direct, str) and direct:
        return direct
    many = getattr(exc, "request_ids", None)
    if isinstance(many, list | tuple) and many and isinstance(many[0], str) and many[0]:
        return many[0]
    response = getattr(exc, "response", None)
    if response is not None:
        return _request_id_from_response_headers(response)
    return None


def _mapping_from_status(status: int) -> tuple[str, ErrorCategory, bool]:
    if status == 401:
        return "PROVIDER_AUTHENTICATION_FAILED", ErrorCategory.AUTHENTICATION, False
    if status == 403:
        return "PROVIDER_FORBIDDEN", ErrorCategory.AUTHORIZATION, False
    if status == 404:
        return "PROVIDER_RESOURCE_NOT_FOUND", ErrorCategory.NOT_FOUND, False
    if status == 408:
        return "PROVIDER_TIMEOUT", ErrorCategory.TIMEOUT, True
    if status == 409:
        return "PROVIDER_CONFLICT", ErrorCategory.CONFLICT, False
    if status == 429:
        return "PROVIDER_RATE_LIMITED", ErrorCategory.RATE_LIMIT, True
    if 500 <= status <= 599:
        return "PROVIDER_UNAVAILABLE", ErrorCategory.PROVIDER, True
    return "PROVIDER_INTERNAL_ERROR", ErrorCategory.PROVIDER, False


def _mapping_from_exception_class(exc: BaseException) -> tuple[str, ErrorCategory, bool]:
    if isinstance(exc, os_exc.ForbiddenException):
        return "PROVIDER_FORBIDDEN", ErrorCategory.AUTHORIZATION, False
    if isinstance(exc, os_exc.ResourceNotFound | os_exc.NotFoundException):
        return "PROVIDER_RESOURCE_NOT_FOUND", ErrorCategory.NOT_FOUND, False
    if isinstance(exc, os_exc.ConflictException):
        return "PROVIDER_CONFLICT", ErrorCategory.CONFLICT, False
    if isinstance(
        exc,
        TimeoutError | os_exc.ResourceTimeout | req_exc.Timeout | ks_exc.ConnectTimeout,
    ):
        return "PROVIDER_TIMEOUT", ErrorCategory.TIMEOUT, True
    if isinstance(
        exc,
        ConnectionError
        | req_exc.ConnectionError
        | ks_exc.ConnectFailure
        | ks_exc.RetriableConnectionFailure,
    ):
        return "PROVIDER_NETWORK_ERROR", ErrorCategory.NETWORK, True
    return "PROVIDER_INTERNAL_ERROR", ErrorCategory.PROVIDER, False


def normalize_openstack_exception(
    exc: BaseException,
    *,
    service: str | None = None,
) -> CommonError:
    status = _http_status(exc)
    if status is not None:
        code, category, retryable = _mapping_from_status(status)
    else:
        code, category, retryable = _mapping_from_exception_class(exc)
    return CommonError(
        code=code,
        message=_PUBLIC_MESSAGE,
        category=category,
        retryable=retryable,
        provider="OPENSTACK",
        provider_service=service,
        provider_request_id=_request_id(exc),
        details={},
    )
