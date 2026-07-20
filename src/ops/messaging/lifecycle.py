"""Worker intake and graceful shutdown helpers."""

from __future__ import annotations

from typing import Literal


class WorkerLifecycle:
    """Track whether the worker accepts new work and in-flight messages."""

    def __init__(self) -> None:
        self._accepting = True
        self._in_flight: set[str] = set()

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
        self._in_flight.add(message_id)

    def finish_or_nack(
        self,
        message_id: str,
        *,
        completed: bool,
    ) -> Literal["ack", "nack"]:
        self._in_flight.discard(message_id)
        return "ack" if completed else "nack"
