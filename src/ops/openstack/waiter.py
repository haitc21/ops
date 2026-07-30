"""Deterministic OpenStack state waiters used by lifecycle handlers."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


class WaiterTimeoutError(TimeoutError):
    """Provider resource did not reach the requested state before the deadline."""


class WaiterProviderError(RuntimeError):
    """Provider resource entered an unrecoverable state while waiting."""

    def __init__(
        self,
        message: str,
        *,
        resource: Any | None = None,
        reason: str = "error_state",
    ) -> None:
        super().__init__(message)
        self.resource = resource
        self.reason = reason


@dataclass(frozen=True, slots=True)
class WaiterConfig:
    target_states: frozenset[str]
    terminal_error_states: frozenset[str] = frozenset({"ERROR"})
    deleted_states: frozenset[str] = frozenset({"DELETED", "DELETED_COMPLETE"})
    interval_seconds: float = 1.0
    timeout_seconds: float = 300.0

    def __post_init__(self) -> None:
        if not self.target_states or self.interval_seconds <= 0 or self.timeout_seconds <= 0:
            raise ValueError("waiter configuration is invalid")


async def wait_for_state(
    fetch: Callable[[], Awaitable[Any]],
    *,
    config: WaiterConfig,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> Any:
    """Poll a provider resource without treating polling as command retry."""
    deadline = monotonic() + config.timeout_seconds
    while True:
        resource = await fetch()
        state = str(getattr(resource, "status", "")).upper()
        if state in config.target_states:
            return resource
        if state in config.terminal_error_states:
            raise WaiterProviderError(
                "provider resource entered an error state",
                resource=resource,
                reason="error_state",
            )
        if state in config.deleted_states:
            raise WaiterProviderError(
                "provider resource disappeared while waiting",
                resource=resource,
                reason="deleted",
            )
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise WaiterTimeoutError("provider state waiter timed out")
        await sleep(min(config.interval_seconds, remaining))


async def wait_for_deleted(
    fetch: Callable[[], Awaitable[Any]],
    *,
    config: WaiterConfig,
    not_found_exceptions: tuple[type[BaseException], ...],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    """Wait until a provider reports a resource as absent.

    OpenStack commonly confirms deletion by returning 404 rather than by
    exposing a stable DELETED resource state. The exception tuple keeps this
    generic waiter independent of any provider SDK.
    """
    deadline = monotonic() + config.timeout_seconds
    while True:
        try:
            resource = await fetch()
        except not_found_exceptions:
            return
        state = str(getattr(resource, "status", "")).upper()
        if state in config.deleted_states:
            return
        if state in config.terminal_error_states:
            raise WaiterProviderError(
                "provider resource entered an error state",
                resource=resource,
                reason="error_state",
            )
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise WaiterTimeoutError("provider deletion waiter timed out")
        await sleep(min(config.interval_seconds, remaining))
