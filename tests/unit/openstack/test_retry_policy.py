"""OPS-103: deterministic retry classification."""

from __future__ import annotations

import math

import pytest

from ops.contracts.errors import CommonError, ErrorCategory
from ops.openstack.retry import RetryDecision, classify_retry


def test_retry_uses_retry_after_when_present() -> None:
    error = CommonError(
        code="PROVIDER_RATE_LIMITED",
        message="limited",
        category=ErrorCategory.RATE_LIMIT,
        retryable=True,
    )
    decision = classify_retry(error, attempt=1, retry_after=17.0)
    assert decision == RetryDecision(retryable=True, exhausted=False, delay_seconds=17.0)


def test_retry_uses_exponential_backoff_and_jitter() -> None:
    error = CommonError(
        code="PROVIDER_TIMEOUT",
        message="timeout",
        category=ErrorCategory.TIMEOUT,
        retryable=True,
    )
    decision = classify_retry(error, attempt=3, random_unit=0.5)
    assert decision.delay_seconds == 5.0


def test_non_retryable_and_exhausted_have_no_delay() -> None:
    fatal = CommonError(
        code="PROVIDER_FORBIDDEN",
        message="forbidden",
        category=ErrorCategory.AUTHORIZATION,
        retryable=False,
    )
    transient = CommonError(
        code="PROVIDER_TIMEOUT",
        message="timeout",
        category=ErrorCategory.TIMEOUT,
        retryable=True,
    )
    assert classify_retry(fatal, attempt=1) == RetryDecision(False, False, None)
    assert classify_retry(transient, attempt=5, max_attempts=5) == RetryDecision(False, True, None)


@pytest.mark.parametrize(
    ("attempt", "max_attempts", "random_unit"),
    (
        (0, 5, 0.5),
        (1, 0, 0.5),
        (1, 5, -0.1),
        (1, 5, 1.1),
    ),
)
def test_invalid_retry_parameters_raise(
    attempt: int,
    max_attempts: int,
    random_unit: float,
) -> None:
    error = CommonError(
        code="PROVIDER_TIMEOUT",
        message="timeout",
        category=ErrorCategory.TIMEOUT,
        retryable=True,
    )
    with pytest.raises(ValueError, match="invalid retry parameters"):
        classify_retry(error, attempt=attempt, max_attempts=max_attempts, random_unit=random_unit)


@pytest.mark.parametrize("retry_after", (math.nan, math.inf, -math.inf))
def test_non_finite_retry_after_raises(retry_after: float) -> None:
    error = CommonError(
        code="PROVIDER_RATE_LIMITED",
        message="limited",
        category=ErrorCategory.RATE_LIMIT,
        retryable=True,
    )

    with pytest.raises(ValueError, match="invalid retry parameters"):
        classify_retry(error, attempt=1, retry_after=retry_after)


def test_large_attempt_is_capped_without_overflow() -> None:
    error = CommonError(
        code="PROVIDER_TIMEOUT",
        message="timeout",
        category=ErrorCategory.TIMEOUT,
        retryable=True,
    )

    decision = classify_retry(error, attempt=1025, max_attempts=2000, random_unit=1.0)

    assert decision == RetryDecision(True, False, 90.0)
