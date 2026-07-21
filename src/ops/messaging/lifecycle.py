"""Worker intake and graceful shutdown helpers."""

from __future__ import annotations

import asyncio
from typing import Literal


class WorkerLifecycle:
    """Track whether the worker accepts new work and in-flight messages."""

    def __init__(self) -> None:
        self._accepting = True
        self._in_flight: dict[str, int] = {}
        self._drain_event = asyncio.Event()
        self._drain_event.set()

    @property
    def accepting_work(self) -> bool:
        return self._accepting

    @property
    def in_flight(self) -> set[str]:
        return set(self._in_flight)

    @property
    def is_drained(self) -> bool:
        return not self._in_flight

    def begin_shutdown(self) -> None:
        self._accepting = False

    def mark_in_flight(self, message_id: str) -> None:
        if not self._accepting:
            raise RuntimeError("worker is shutting down and cannot accept new work")
        self._in_flight[message_id] = self._in_flight.get(message_id, 0) + 1
        self._drain_event.clear()

    def finish_or_nack(
        self,
        message_id: str,
        *,
        completed: bool,
    ) -> Literal["ack", "nack"]:
        count = self._in_flight.get(message_id, 0)
        if count > 1:
            self._in_flight[message_id] = count - 1
        elif count == 1:
            del self._in_flight[message_id]
        if not self._in_flight:
            self._drain_event.set()
        return "ack" if completed else "nack"

    async def wait_drained(self, drain_timeout: float) -> bool:
        if not self._in_flight:
            return True
        try:
            await asyncio.wait_for(self._drain_event.wait(), drain_timeout)
        except TimeoutError:
            return False
        return not self._in_flight
