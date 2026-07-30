"""OPS-406 deterministic waiter tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ops.openstack.waiter import (
    WaiterConfig,
    WaiterProviderError,
    WaiterTimeoutError,
    wait_for_deleted,
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
    resource = SimpleNamespace(
        id="server-1",
        status="ERROR",
        fault={"message": "NoValidHost", "code": 500, "details": "traceback must not leak"},
    )

    async def fetch() -> SimpleNamespace:
        return resource

    with pytest.raises(WaiterProviderError) as exc_info:
        asyncio.run(
            wait_for_state(
                fetch,
                config=WaiterConfig(target_states=frozenset({"ACTIVE"})),
            )
        )

    error = exc_info.value
    assert error.resource is resource
    assert error.reason == "error_state"


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


def test_deletion_waiter_accepts_provider_not_found() -> None:
    class ResourceGone(Exception):
        pass

    responses = iter([SimpleNamespace(status="ACTIVE"), ResourceGone()])

    async def fetch() -> SimpleNamespace:
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    asyncio.run(
        wait_for_deleted(
            fetch,
            config=WaiterConfig(target_states=frozenset({"DELETED"}), interval_seconds=0.01),
            not_found_exceptions=(ResourceGone,),
            sleep=lambda _delay: asyncio.sleep(0),
        )
    )


def test_deletion_waiter_times_out_without_claiming_deleted() -> None:
    async def fetch() -> SimpleNamespace:
        return SimpleNamespace(status="ACTIVE")

    with pytest.raises(WaiterTimeoutError, match="deletion"):
        asyncio.run(
            wait_for_deleted(
                fetch,
                config=WaiterConfig(
                    target_states=frozenset({"DELETED"}),
                    interval_seconds=1,
                    timeout_seconds=1,
                ),
                not_found_exceptions=(LookupError,),
                sleep=lambda _delay: asyncio.sleep(0),
                monotonic=iter([0.0, 2.0]).__next__,
            )
        )
