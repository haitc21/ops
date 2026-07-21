"""OPS-003: graceful shutdown intake lifecycle."""

from __future__ import annotations

import pytest

from ops.messaging.lifecycle import WorkerLifecycle


def test_shutdown_stops_intake_and_drains() -> None:
    lifecycle = WorkerLifecycle()
    assert lifecycle.accepting_work is True

    lifecycle.mark_in_flight("msg-1")
    lifecycle.begin_shutdown()
    assert lifecycle.accepting_work is False
    assert lifecycle.in_flight == {"msg-1"}

    decision = lifecycle.finish_or_nack("msg-1", completed=True)
    assert decision == "ack"
    assert lifecycle.in_flight == set()
    assert lifecycle.is_drained is True


def test_shutdown_rejects_new_intake() -> None:
    lifecycle = WorkerLifecycle()
    lifecycle.begin_shutdown()
    with pytest.raises(RuntimeError, match="shutting down"):
        lifecycle.mark_in_flight("msg-new")


def test_shutdown_nacks_unfinished_work() -> None:
    lifecycle = WorkerLifecycle()
    lifecycle.mark_in_flight("msg-2")
    lifecycle.begin_shutdown()
    assert lifecycle.finish_or_nack("msg-2", completed=False) == "nack"


@pytest.mark.asyncio
async def test_duplicate_message_ids_remain_in_flight_until_both_finish() -> None:
    lifecycle = WorkerLifecycle()
    lifecycle.mark_in_flight("same-id")
    lifecycle.mark_in_flight("same-id")

    lifecycle.finish_or_nack("same-id", completed=True)
    assert await lifecycle.wait_drained(0.01) is False

    lifecycle.finish_or_nack("same-id", completed=True)
    assert await lifecycle.wait_drained(0.01) is True
