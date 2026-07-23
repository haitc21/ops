"""OPS-406 deterministic waiter tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ops.openstack.waiter import (
    WaiterConfig,
    WaiterProviderError,
    WaiterTimeoutError,
    wait_for_state,
)


def test_waiter_reaches_target_with_injected_clock_and_sleeper() -> None:
    states = iter(["BUILD", "BUILD", "ACTIVE"])
    now = 0.0
    sleeps: list[float] = []

    def monotonic() -> float:
        return now

    async def sleep(delay: float) -> None:
        nonlocal now
        sleeps.append(delay)
        now += delay

    async def fetch() -> SimpleNamespace:
        return SimpleNamespace(status=next(states))

    result = asyncio.run(
        wait_for_state(
            fetch,
            config=WaiterConfig(target_states=frozenset({"ACTIVE"}), interval_seconds=2),
            sleep=sleep,
            monotonic=monotonic,
        )
    )
    assert result.status == "ACTIVE"
    assert sleeps == [2.0, 2.0]


def test_waiter_distinguishes_provider_error() -> None:
    async def fetch() -> SimpleNamespace:
        return SimpleNamespace(status="ERROR")

    with pytest.raises(WaiterProviderError):
        asyncio.run(
            wait_for_state(
                fetch,
                config=WaiterConfig(target_states=frozenset({"ACTIVE"})),
            )
        )


def test_waiter_times_out_without_retrying_command() -> None:
    async def fetch() -> SimpleNamespace:
        return SimpleNamespace(status="BUILD")

    with pytest.raises(WaiterTimeoutError):
        asyncio.run(
            wait_for_state(
                fetch,
                config=WaiterConfig(
                    target_states=frozenset({"ACTIVE"}), interval_seconds=1, timeout_seconds=1
                ),
                sleep=lambda _delay: asyncio.sleep(0),
                monotonic=iter([0.0, 2.0]).__next__,
            )
        )
