"""OPS-102 unit tests for RabbitMQ topology declaration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import aio_pika
import pytest

# Independent contract literals — not imported from production constants.
EXPECTED_EXCHANGES: dict[str, str] = {
    "cmp.cloud.command.v1": "topic",
    "cmp.cloud.event.v1": "topic",
    "cmp.cloud.retry.v1": "direct",
    "cmp.cloud.dlx.v1": "topic",
}
EXPECTED_COMMAND_QUEUE = "ops.command.v1"
EXPECTED_COMMAND_BINDINGS = ("openstack.#", "cloud.operation.command.#")
EXPECTED_COMMAND_QUEUE_ARGS = {
    "x-dead-letter-exchange": "cmp.cloud.dlx.v1",
    "x-dead-letter-routing-key": "ops.command.dlq",
}
EXPECTED_RETRY_QUEUES: tuple[dict[str, Any], ...] = (
    {
        "queue": "ops.command.retry.1.v1",
        "routing_key": "ops.command.retry.1",
        "arguments": {
            "x-message-ttl": 30_000,
            "x-dead-letter-exchange": "cmp.cloud.command.v1",
            "x-dead-letter-routing-key": "openstack.retry",
        },
    },
    {
        "queue": "ops.command.retry.2.v1",
        "routing_key": "ops.command.retry.2",
        "arguments": {
            "x-message-ttl": 120_000,
            "x-dead-letter-exchange": "cmp.cloud.command.v1",
            "x-dead-letter-routing-key": "openstack.retry",
        },
    },
)
EXPECTED_DLQ = {
    "queue": "ops.command.dlq.v1",
    "routing_key": "ops.command.dlq",
}
CPS_OWNED_QUEUES = (
    "cps.cloud.event.v1",
    "cps.cloud.event.retry.1.v1",
    "cps.cloud.event.retry.2.v1",
    "cps.cloud.event.dlq.v1",
)


@dataclass
class RecordedCall:
    method: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


@dataclass
class FakeExchange:
    name: str
    channel: FakeTopologyChannel

    async def bind(self, exchange: FakeExchange, routing_key: str = "", **kwargs: Any) -> None:
        self.channel.record("exchange.bind", (self.name, exchange.name, routing_key), kwargs)


@dataclass
class FakeQueue:
    name: str
    channel: FakeTopologyChannel

    async def bind(
        self,
        exchange: FakeExchange | str,
        routing_key: str = "",
        **kwargs: Any,
    ) -> None:
        exchange_name = exchange.name if isinstance(exchange, FakeExchange) else exchange
        self.channel.record(
            "queue.bind",
            (self.name, exchange_name, routing_key),
            kwargs,
        )


@dataclass
class FakeTopologyChannel:
    calls: list[RecordedCall] = field(default_factory=list)
    fail_on_exchange: str | None = None

    def record(self, method: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        self.calls.append(RecordedCall(method=method, args=args, kwargs=kwargs))

    async def declare_exchange(
        self,
        name: str,
        type: aio_pika.ExchangeType | str = aio_pika.ExchangeType.DIRECT,
        **kwargs: Any,
    ) -> FakeExchange:
        if self.fail_on_exchange == name:
            msg = f"precondition failed for exchange {name}"
            raise RuntimeError(msg)
        self.record("declare_exchange", (name, type), kwargs)
        return FakeExchange(name=name, channel=self)

    async def declare_queue(self, name: str | None = None, **kwargs: Any) -> FakeQueue:
        assert name is not None
        self.record("declare_queue", (name,), kwargs)
        return FakeQueue(name=name, channel=self)


def _exchange_calls(channel: FakeTopologyChannel) -> list[RecordedCall]:
    return [call for call in channel.calls if call.method == "declare_exchange"]


def _queue_calls(channel: FakeTopologyChannel) -> list[RecordedCall]:
    return [call for call in channel.calls if call.method == "declare_queue"]


def _bind_calls(channel: FakeTopologyChannel) -> list[RecordedCall]:
    return [call for call in channel.calls if call.method == "queue.bind"]


@pytest.mark.asyncio
async def test_declares_exact_exchange_names_and_types() -> None:
    from ops.messaging.topology import TopologyBuilder

    channel = FakeTopologyChannel()
    await TopologyBuilder().declare(channel)

    declared = {call.args[0]: call.args[1] for call in _exchange_calls(channel)}
    assert declared == EXPECTED_EXCHANGES


@pytest.mark.asyncio
async def test_exchanges_are_durable_and_not_auto_deleted() -> None:
    from ops.messaging.topology import TopologyBuilder

    channel = FakeTopologyChannel()
    await TopologyBuilder().declare(channel)

    for call in _exchange_calls(channel):
        assert call.kwargs["durable"] is True
        assert call.kwargs["auto_delete"] is False
        assert call.kwargs.get("passive") is not True


@pytest.mark.asyncio
async def test_main_command_queue_has_exact_arguments() -> None:
    from ops.messaging.topology import TopologyBuilder

    channel = FakeTopologyChannel()
    await TopologyBuilder().declare(channel)

    command_call = next(
        call for call in _queue_calls(channel) if call.args[0] == EXPECTED_COMMAND_QUEUE
    )
    assert command_call.kwargs["durable"] is True
    assert command_call.kwargs["auto_delete"] is False
    assert command_call.kwargs["exclusive"] is False
    assert command_call.kwargs["arguments"] == EXPECTED_COMMAND_QUEUE_ARGS


@pytest.mark.asyncio
async def test_main_command_queue_binds_expected_patterns() -> None:
    from ops.messaging.topology import TopologyBuilder

    channel = FakeTopologyChannel()
    await TopologyBuilder().declare(channel)

    bindings = {
        (call.args[0], call.args[1], call.args[2])
        for call in _bind_calls(channel)
        if call.args[0] == EXPECTED_COMMAND_QUEUE
    }
    expected = {
        (EXPECTED_COMMAND_QUEUE, "cmp.cloud.command.v1", key) for key in EXPECTED_COMMAND_BINDINGS
    }
    assert bindings == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_spec", EXPECTED_RETRY_QUEUES, ids=lambda spec: spec["queue"])
async def test_retry_queue_exact_ttl_dlx_binding(retry_spec: dict[str, Any]) -> None:
    from ops.messaging.topology import TopologyBuilder

    channel = FakeTopologyChannel()
    await TopologyBuilder().declare(channel)

    queue_call = next(call for call in _queue_calls(channel) if call.args[0] == retry_spec["queue"])
    assert queue_call.kwargs["arguments"] == retry_spec["arguments"]
    bind = next(call for call in _bind_calls(channel) if call.args[0] == retry_spec["queue"])
    assert bind.args[1] == "cmp.cloud.retry.v1"
    assert bind.args[2] == retry_spec["routing_key"]


@pytest.mark.asyncio
async def test_dlq_queue_binds_to_dlx_exchange() -> None:
    from ops.messaging.topology import TopologyBuilder

    channel = FakeTopologyChannel()
    await TopologyBuilder().declare(channel)

    dlq_call = next(call for call in _queue_calls(channel) if call.args[0] == EXPECTED_DLQ["queue"])
    assert dlq_call.kwargs["durable"] is True
    assert dlq_call.kwargs["auto_delete"] is False
    assert dlq_call.kwargs["exclusive"] is False
    bind = next(call for call in _bind_calls(channel) if call.args[0] == EXPECTED_DLQ["queue"])
    assert bind.args[1] == "cmp.cloud.dlx.v1"
    assert bind.args[2] == EXPECTED_DLQ["routing_key"]


@pytest.mark.asyncio
async def test_does_not_declare_cps_owned_queues() -> None:
    from ops.messaging.topology import TopologyBuilder

    channel = FakeTopologyChannel()
    await TopologyBuilder().declare(channel)

    declared_queues = {call.args[0] for call in _queue_calls(channel)}
    assert declared_queues.isdisjoint(CPS_OWNED_QUEUES)


@pytest.mark.asyncio
async def test_declaration_order_exchanges_before_queues_and_bindings() -> None:
    from ops.messaging.topology import TopologyBuilder

    channel = FakeTopologyChannel()
    await TopologyBuilder().declare(channel)

    methods = [call.method for call in channel.calls]
    last_exchange = max(i for i, method in enumerate(methods) if method == "declare_exchange")
    first_queue = min(i for i, method in enumerate(methods) if method == "declare_queue")
    first_bind = min(i for i, method in enumerate(methods) if method == "queue.bind")
    assert last_exchange < first_queue < first_bind


@pytest.mark.asyncio
async def test_declare_twice_preserves_unique_binding_set() -> None:
    from ops.messaging.topology import TopologyBuilder

    channel = FakeTopologyChannel()
    builder = TopologyBuilder()
    await builder.declare(channel)
    first_bindings = {(call.args[0], call.args[1], call.args[2]) for call in _bind_calls(channel)}
    await builder.declare(channel)
    second_bindings = {(call.args[0], call.args[1], call.args[2]) for call in _bind_calls(channel)}
    assert first_bindings == second_bindings
    assert len(second_bindings) == len(first_bindings)


@pytest.mark.asyncio
async def test_incompatible_exchange_declaration_is_not_swallowed() -> None:
    from ops.messaging.topology import TopologyBuilder

    channel = FakeTopologyChannel()
    channel.fail_on_exchange = "cmp.cloud.command.v1"
    with pytest.raises(RuntimeError, match="precondition failed"):
        await TopologyBuilder().declare(channel)
