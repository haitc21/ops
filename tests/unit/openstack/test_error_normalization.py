"""OPS-103: OpenStack SDK exception normalization."""

from __future__ import annotations

from types import SimpleNamespace

import keystoneauth1.exceptions.connection as ks_exc
import keystoneauth1.exceptions.http as ks_http
import pytest
import requests
import requests.exceptions as req_exc
from openstack import exceptions as os_exc

from ops.openstack.errors import (
    normalize_openstack_exception,
    normalize_waiter_provider_error,
    normalize_waiter_timeout_error,
)
from ops.openstack.waiter import WaiterProviderError, WaiterTimeoutError


def _synthetic_secret() -> str:
    keyword = "pass" + "word"
    return f"{keyword}=must-not-leak"


class _FakeResponse:
    text = "secret-body"


class _FakeHttpStatusException(Exception):
    http_status = 502
    request_id = "req-legacy-http-status"


class _FakeStatusCodeException(Exception):
    status_code = 502
    request_ids = ("req-from-tuple", "req-second")


class _FakeExceptionWithResponse(Exception):
    status_code = 502
    request_id = "req-safe"
    response = _FakeResponse()


def _http_exception(
    status_code: int,
    message: str | None = None,
) -> os_exc.HttpException:
    response = requests.Response()
    response.status_code = status_code
    resolved_message = message if message is not None else f"provider error {_synthetic_secret()}"
    return os_exc.HttpException(resolved_message, response=response)


def _os_exception_with_status(
    exc_type: type[BaseException],
    status_code: int,
    message: str = "synthetic",
) -> BaseException:
    response = requests.Response()
    response.status_code = status_code
    return exc_type(message, response=response)


def _requests_http_error(
    status_code: int,
    *,
    headers: dict[str, str] | None = None,
    request_id: str | None = None,
) -> req_exc.HTTPError:
    response = requests.Response()
    response.status_code = status_code
    if headers:
        response.headers.update(headers)
    response._content = b"response-body-must-not-leak"
    exc = req_exc.HTTPError(response=response)
    if request_id is not None:
        exc.request_id = request_id
    return exc


@pytest.mark.parametrize(
    ("exc", "code", "category", "retryable"),
    (
        (_http_exception(401), "PROVIDER_AUTHENTICATION_FAILED", "AUTHENTICATION", False),
        (
            os_exc.ForbiddenException(f"forbidden {_synthetic_secret()}"),
            "PROVIDER_FORBIDDEN",
            "AUTHORIZATION",
            False,
        ),
        (_http_exception(403), "PROVIDER_FORBIDDEN", "AUTHORIZATION", False),
        (
            os_exc.ResourceNotFound(f"missing {_synthetic_secret()}"),
            "PROVIDER_RESOURCE_NOT_FOUND",
            "NOT_FOUND",
            False,
        ),
        (_http_exception(404), "PROVIDER_RESOURCE_NOT_FOUND", "NOT_FOUND", False),
        (
            os_exc.ConflictException(f"conflict {_synthetic_secret()}"),
            "PROVIDER_CONFLICT",
            "CONFLICT",
            False,
        ),
        (_http_exception(409), "PROVIDER_CONFLICT", "CONFLICT", False),
        (_http_exception(429), "PROVIDER_RATE_LIMITED", "RATE_LIMIT", True),
        (_http_exception(503), "PROVIDER_UNAVAILABLE", "PROVIDER", True),
        (TimeoutError(f"timeout {_synthetic_secret()}"), "PROVIDER_TIMEOUT", "TIMEOUT", True),
        (
            ConnectionError(f"network {_synthetic_secret()}"),
            "PROVIDER_NETWORK_ERROR",
            "NETWORK",
            True,
        ),
        (
            os_exc.ResourceTimeout(f"resource timeout {_synthetic_secret()}"),
            "PROVIDER_TIMEOUT",
            "TIMEOUT",
            True,
        ),
    ),
)
def test_normalization(
    exc: BaseException,
    code: str,
    category: str,
    retryable: bool,
) -> None:
    error = normalize_openstack_exception(exc, service="compute")
    assert (error.code, error.category.value, error.retryable) == (code, category, retryable)
    assert error.provider == "OPENSTACK"
    assert error.provider_service == "compute"
    assert error.message == "OpenStack provider request failed"
    assert "response" not in error.details
    assert _synthetic_secret() not in error.model_dump_json()


@pytest.mark.parametrize(
    ("exc", "code", "category", "retryable"),
    (
        (
            req_exc.Timeout(f"requests timeout {_synthetic_secret()}"),
            "PROVIDER_TIMEOUT",
            "TIMEOUT",
            True,
        ),
        (
            req_exc.ConnectTimeout(f"requests connect timeout {_synthetic_secret()}"),
            "PROVIDER_TIMEOUT",
            "TIMEOUT",
            True,
        ),
        (
            req_exc.ConnectionError(f"requests connection {_synthetic_secret()}"),
            "PROVIDER_NETWORK_ERROR",
            "NETWORK",
            True,
        ),
    ),
)
def test_requests_exception_normalization(
    exc: BaseException,
    code: str,
    category: str,
    retryable: bool,
) -> None:
    error = normalize_openstack_exception(exc, service="network")
    assert (error.code, error.category.value, error.retryable) == (code, category, retryable)
    assert error.message == "OpenStack provider request failed"
    assert _synthetic_secret() not in error.model_dump_json()


@pytest.mark.parametrize(
    ("exc", "code", "category", "retryable"),
    (
        (
            ks_exc.ConnectTimeout(f"keystone timeout {_synthetic_secret()}"),
            "PROVIDER_TIMEOUT",
            "TIMEOUT",
            True,
        ),
        (
            ks_exc.ConnectFailure(f"keystone failure {_synthetic_secret()}"),
            "PROVIDER_NETWORK_ERROR",
            "NETWORK",
            True,
        ),
        (
            ks_exc.RetriableConnectionFailure(f"keystone retriable {_synthetic_secret()}"),
            "PROVIDER_NETWORK_ERROR",
            "NETWORK",
            True,
        ),
    ),
)
def test_keystone_exception_normalization(
    exc: BaseException,
    code: str,
    category: str,
    retryable: bool,
) -> None:
    error = normalize_openstack_exception(exc, service="identity")
    assert (error.code, error.category.value, error.retryable) == (code, category, retryable)
    assert error.message == "OpenStack provider request failed"
    assert _synthetic_secret() not in error.model_dump_json()


def test_http_503_not_classified_as_network() -> None:
    error = normalize_openstack_exception(_http_exception(503), service="compute")
    assert (error.code, error.category.value, error.retryable) == (
        "PROVIDER_UNAVAILABLE",
        "PROVIDER",
        True,
    )


def test_unknown_sdk_exception_is_internal_non_retryable() -> None:
    class UnknownSDK(os_exc.SDKException):
        pass

    error = normalize_openstack_exception(
        UnknownSDK(f"unknown {_synthetic_secret()}"),
        service="compute",
    )
    assert (error.code, error.category.value, error.retryable) == (
        "PROVIDER_INTERNAL_ERROR",
        "PROVIDER",
        False,
    )
    assert _synthetic_secret() not in error.model_dump_json()


def test_legacy_http_status_attribute_still_supported() -> None:
    error = normalize_openstack_exception(_FakeHttpStatusException(), service="compute")
    assert (error.code, error.category.value, error.retryable) == (
        "PROVIDER_UNAVAILABLE",
        "PROVIDER",
        True,
    )
    assert error.provider_request_id == "req-legacy-http-status"


def test_request_id_from_request_ids_tuple() -> None:
    error = normalize_openstack_exception(_FakeStatusCodeException(), service="volume")
    assert error.provider_request_id == "req-from-tuple"


def test_request_id_retained_and_response_body_excluded() -> None:
    error = normalize_openstack_exception(_FakeExceptionWithResponse(), service="identity")
    assert error.provider_request_id == "req-safe"
    assert "secret-body" not in error.model_dump_json()
    assert "response" not in error.details


@pytest.mark.parametrize(
    ("exc", "code", "category", "retryable"),
    (
        (
            _os_exception_with_status(os_exc.ForbiddenException, 404),
            "PROVIDER_RESOURCE_NOT_FOUND",
            "NOT_FOUND",
            False,
        ),
        (
            _os_exception_with_status(os_exc.ResourceNotFound, 409),
            "PROVIDER_CONFLICT",
            "CONFLICT",
            False,
        ),
        (
            _os_exception_with_status(os_exc.ConflictException, 503),
            "PROVIDER_UNAVAILABLE",
            "PROVIDER",
            True,
        ),
    ),
)
def test_http_status_precedence_over_exception_class(
    exc: BaseException,
    code: str,
    category: str,
    retryable: bool,
) -> None:
    error = normalize_openstack_exception(exc, service="compute")
    assert (error.code, error.category.value, error.retryable) == (code, category, retryable)


def test_keystone_request_timeout_http_408_maps_to_provider_timeout() -> None:
    error = normalize_openstack_exception(ks_http.RequestTimeout("synthetic"), service="identity")
    assert (error.code, error.category.value, error.retryable) == (
        "PROVIDER_TIMEOUT",
        "TIMEOUT",
        True,
    )
    assert error.message == "OpenStack provider request failed"


def test_request_id_from_x_openstack_request_id_header() -> None:
    error = normalize_openstack_exception(
        _requests_http_error(
            502,
            headers={
                "x-openstack-request-id": "req-openstack-header",
                "Authorization": "Bearer secret-token",
            },
        ),
        service="compute",
    )
    assert error.provider_request_id == "req-openstack-header"
    dumped = error.model_dump_json()
    assert "secret-token" not in dumped
    assert "response-body-must-not-leak" not in dumped
    assert _synthetic_secret() not in dumped
    assert "response" not in error.details
    assert "Authorization" not in error.details


def test_request_id_from_x_request_id_header() -> None:
    error = normalize_openstack_exception(
        _requests_http_error(502, headers={"x-request-id": "req-generic-header"}),
        service="compute",
    )
    assert error.provider_request_id == "req-generic-header"


def test_direct_request_id_precedence_over_response_header() -> None:
    error = normalize_openstack_exception(
        _requests_http_error(
            502,
            headers={"x-openstack-request-id": "req-from-header"},
            request_id="req-direct-attribute",
        ),
        service="compute",
    )
    assert error.provider_request_id == "req-direct-attribute"


def test_waiter_timeout_preserves_openstack_compute_context() -> None:
    error = normalize_waiter_timeout_error(
        WaiterTimeoutError("provider state waiter timed out"),
        service="compute",
    )
    assert (error.code, error.category.value, error.retryable) == (
        "PROVIDER_TIMEOUT",
        "TIMEOUT",
        True,
    )
    assert error.provider == "OPENSTACK"
    assert error.provider_service == "compute"
    assert error.message == "OpenStack provider request timed out"


@pytest.mark.parametrize(
    "fault_message",
    (
        "NoValidHost",
        "NoValidHostWasFound",
        "No valid host was found.",
        "No valid host was found",
    ),
)
def test_waiter_provider_error_maps_novalidhost_variants_to_insufficient_capacity(
    fault_message: str,
) -> None:
    resource = SimpleNamespace(
        id="server-1",
        status="ERROR",
        request_ids=("req-nova-create",),
        fault={
            "message": fault_message,
            "code": 500,
            "details": "Traceback (most recent call last): secret-body",
        },
    )
    exc = WaiterProviderError(
        "provider resource entered an error state",
        resource=resource,
        reason="error_state",
    )

    error = normalize_waiter_provider_error(exc, service="compute")

    assert (error.code, error.category.value, error.retryable) == (
        "INSUFFICIENT_CAPACITY",
        "QUOTA",
        False,
    )
    assert error.provider == "OPENSTACK"
    assert error.provider_service == "compute"
    assert error.provider_request_id == "req-nova-create"
    assert error.message == "OpenStack provider request failed"
    assert error.details == {
        "provider_status": "ERROR",
        "provider_resource_id": "server-1",
        "provider_fault_code": fault_message,
        "provider_fault_status": 500,
    }
    dumped = error.model_dump_json()
    assert "traceback must not leak" not in dumped
    assert "secret-body" not in dumped


def test_waiter_provider_error_maps_live_novalidhost_fault_shape() -> None:
    """Regression for live Nova controller fault: message text, not exception class name."""
    resource = SimpleNamespace(
        id="server-live",
        status="ERROR",
        request_ids=("req-nova-scheduler",),
        fault={
            "message": "No valid host was found.",
            "code": 500,
            "details": "Traceback (most recent call last): secret-body",
        },
    )
    exc = WaiterProviderError(
        "provider resource entered an error state",
        resource=resource,
        reason="error_state",
    )

    error = normalize_waiter_provider_error(exc, service="compute")

    assert (error.code, error.category.value, error.retryable) == (
        "INSUFFICIENT_CAPACITY",
        "QUOTA",
        False,
    )
    assert error.details == {
        "provider_status": "ERROR",
        "provider_resource_id": "server-live",
        "provider_fault_code": "No valid host was found.",
        "provider_fault_status": 500,
    }
    dumped = error.model_dump_json()
    assert "secret-body" not in dumped
    assert "Traceback" not in dumped


def test_waiter_provider_error_does_not_misclassify_unrelated_host_fault() -> None:
    resource = SimpleNamespace(
        id="server-2",
        status="ERROR",
        fault={
            "message": "Invalid input received: unknown host parameter",
            "code": 400,
        },
    )
    exc = WaiterProviderError(
        "provider resource entered an error state",
        resource=resource,
        reason="error_state",
    )

    error = normalize_waiter_provider_error(exc, service="compute")

    assert (error.code, error.category.value, error.retryable) == (
        "INVALID_RESOURCE_STATE",
        "CONFLICT",
        False,
    )


def test_waiter_provider_error_maps_generic_error_state() -> None:
    resource = SimpleNamespace(
        id="server-2",
        status="ERROR",
        fault={"message": "BuildAbortException", "code": 500},
    )
    exc = WaiterProviderError(
        "provider resource entered an error state",
        resource=resource,
        reason="error_state",
    )

    error = normalize_waiter_provider_error(exc, service="compute")

    assert (error.code, error.category.value, error.retryable) == (
        "INVALID_RESOURCE_STATE",
        "CONFLICT",
        False,
    )
    assert error.details["provider_fault_code"] == "BuildAbortException"


def test_waiter_provider_error_maps_deleted_resource() -> None:
    resource = SimpleNamespace(id="server-3", status="DELETED")
    exc = WaiterProviderError(
        "provider resource disappeared while waiting",
        resource=resource,
        reason="deleted",
    )

    error = normalize_waiter_provider_error(exc, service="compute")

    assert (error.code, error.category.value, error.retryable) == (
        "INVALID_RESOURCE_STATE",
        "CONFLICT",
        False,
    )
    assert error.details == {
        "provider_status": "DELETED",
        "provider_resource_id": "server-3",
    }


def test_empty_request_ids_falls_back_to_response_header() -> None:
    exc = _requests_http_error(
        502,
        headers={"x-openstack-request-id": "req-from-header"},
    )
    exc.request_ids = ("", "req-second")

    error = normalize_openstack_exception(exc, service="compute")

    assert error.provider_request_id == "req-from-header"
