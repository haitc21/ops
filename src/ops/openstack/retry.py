"""Deterministic retry decisions from normalized provider errors."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ops.contracts.errors import CommonError


@dataclass(frozen=True)
class RetryDecision:
    retryable: bool
    exhausted: bool
    delay_seconds: float | None


def classify_retry(
    error: CommonError,
    *,
    attempt: int,
    max_attempts: int = 5,
    retry_after: float | None = None,
    random_unit: float = 0.5,
) -> RetryDecision:
    if (
        attempt < 1
        or max_attempts < 1
        or not 0.0 <= random_unit <= 1.0
        or (retry_after is not None and not math.isfinite(retry_after))
    ):
        raise ValueError("invalid retry parameters")
    if not error.retryable:
        return RetryDecision(False, False, None)
    if attempt >= max_attempts:
        return RetryDecision(False, True, None)
    if retry_after is not None:
        return RetryDecision(True, False, max(0.0, retry_after))
    exponential = 60.0 if attempt >= 7 else float(2 ** (attempt - 1))
    jitter = exponential * 0.5 * random_unit
    return RetryDecision(True, False, exponential + jitter)
