"""Unit tests for correlated event probe helpers."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import pytest
from tests.integration.messaging.helpers import (
    IsolatedDispatchTopology,
    assert_no_correlated_event_for_command,
    event_correlates_with_command,
    get_correlated_event_for_command,
)

from ops.contracts.messages.types import OPERATION_PROGRESS

COMMAND = {
    "message_id": "11111111-1111-4111-8111-111111111111",
    "correlation_id": "22222222-2222-4222-8222-222222222222",
    "operation_id": "33333333-3333-4333-8333-333333333333",
}


def _progress_event(
    *,
    message_type: str = OPERATION_PROGRESS,
    operation_id: str = COMMAND["operation_id"],
    correlation_id: str = COMMAND["correlation_id"],
    causation_id: str = COMMAND["message_id"],
) -> bytes:
    return json.dumps(
        {
            "message_type": message_type,
            "operation_id": operation_id,
            "correlation_id": correlation_id,
            "causation_id": causation_id,
        }
    ).encode()


@dataclass
class FakeProbeMessage:
    body: bytes
    acked: bool = False

    async def ack(self) -> None:
        self.acked = True


@dataclass
class FakeProbeQueue:
    messages: list[FakeProbeMessage] = field(default_factory=list)

    async def get(
        self,
        *,
        timeout: float = 0.0,  # noqa: ASYNC109
        fail: bool = True,
    ) -> FakeProbeMessage | None:
        del timeout, fail
        if not self.messages:
            return None
        return self.messages.pop(0)


@dataclass
class FakeDeletable:
    name: str
    calls: list[str]
    failure: BaseException | None = None

    async def delete(self, **_kwargs) -> None:
        self.calls.append(self.name)
        if self.failure is not None:
            raise self.failure


@pytest.mark.asyncio
async def test_isolated_topology_cleanup_attempts_every_resource() -> None:
    calls: list[str] = []
    topology = IsolatedDispatchTopology(
        command_exchange=FakeDeletable("command_exchange", calls),  # type: ignore[arg-type]
        command_queue=FakeDeletable(  # type: ignore[arg-type]
            "command_queue",
            calls,
            RuntimeError("delete failed"),
        ),
        dlx_exchange=FakeDeletable("dlx_exchange", calls),  # type: ignore[arg-type]
        dlq_queue=FakeDeletable("dlq_queue", calls),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="delete failed"):
        await topology.close()

    assert calls == ["command_queue", "dlq_queue", "command_exchange", "dlx_exchange"]


def test_event_correlates_with_command_matches_identity() -> None:
    assert event_correlates_with_command(_progress_event(), COMMAND) is True


def test_event_correlates_with_command_rejects_unrelated() -> None:
    unrelated = _progress_event(operation_id="99999999-9999-4999-8999-999999999999")
    assert event_correlates_with_command(unrelated, COMMAND) is False


def test_event_correlates_with_command_rejects_wrong_message_type() -> None:
    wrong_type = _progress_event(message_type="cloud.operation.completed")
    assert event_correlates_with_command(wrong_type, COMMAND) is False


@pytest.mark.asyncio
async def test_unrelated_event_does_not_fail_negative_assertion() -> None:
    unrelated = FakeProbeMessage(body=_progress_event(operation_id="other"))
    queue = FakeProbeQueue(messages=[unrelated])
    deadline = asyncio.get_running_loop().time() + 0.5
    await assert_no_correlated_event_for_command(queue, COMMAND, deadline=deadline)
    assert unrelated.acked is True


@pytest.mark.asyncio
async def test_positive_lookup_skips_unrelated_and_finds_correlated() -> None:
    queue = FakeProbeQueue(
        messages=[
            FakeProbeMessage(body=_progress_event(operation_id="other")),
            FakeProbeMessage(body=_progress_event()),
        ]
    )
    deadline = asyncio.get_running_loop().time() + 1.0
    message = await get_correlated_event_for_command(queue, COMMAND, deadline=deadline)
    assert message is not None
    assert event_correlates_with_command(message.body, COMMAND) is True


@pytest.mark.asyncio
async def test_timeout_when_no_correlated_event() -> None:
    queue = FakeProbeQueue(messages=[FakeProbeMessage(body=_progress_event(operation_id="other"))])
    deadline = asyncio.get_running_loop().time() + 0.5
    message = await get_correlated_event_for_command(queue, COMMAND, deadline=deadline)
    assert message is None
