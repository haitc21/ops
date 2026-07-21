"""Test doubles for OPS messaging unit tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ops.messaging.topology import DeclaredTopology


@dataclass
class FakeIncomingMessage:
    body: bytes
    headers: dict[str, Any]
    routing_key: str = "openstack.connection.validate"
    acked: bool = False
    rejected: bool = False
    reject_requeue: bool | None = None

    async def ack(self, multiple: bool = False) -> None:
        self.acked = True

    async def reject(self, requeue: bool = False) -> None:
        self.rejected = True
        self.reject_requeue = requeue


@dataclass
class FakeQueue:
    name: str

    async def consume(self, callback: Any, no_ack: bool = False) -> str:
        _ = callback, no_ack
        return "fake-consumer-tag"

    async def cancel(self, consumer_tag: str) -> None:
        _ = consumer_tag


@dataclass
class FakeExchange:
    name: str = "fake"


@dataclass
class FakePublisher:
    publishes: list[dict[str, Any]] = field(default_factory=list)
    fail_at_index: int | None = None

    async def publish(
        self,
        exchange: FakeExchange,
        routing_key: str,
        body: bytes,
        *,
        headers: dict[str, Any] | None = None,
        mandatory: bool = True,
        confirm_timeout: float | None = 10.0,
    ) -> None:
        from ops.messaging.publisher import PublishConfirmError

        if self.fail_at_index == len(self.publishes):
            raise PublishConfirmError("simulated_confirm_failure")
        self.publishes.append(
            {
                "exchange": exchange.name,
                "routing_key": routing_key,
                "body": body,
                "headers": headers or {},
            }
        )


@dataclass
class FakeChannel:
    closed: bool = False
    prefetch: int | None = None

    async def set_qos(self, prefetch_count: int = 0, **kwargs: Any) -> None:
        self.prefetch = prefetch_count

    async def close(self) -> None:
        self.closed = True


def fresh_delivery_headers(
    *,
    attempt: int = 1,
    max_attempts: int = 3,
    message_id: str = "11111111-1111-4111-8111-111111111111",
    correlation_id: str = "22222222-2222-4222-8222-222222222222",
) -> dict[str, Any]:
    headers = {
        "x-transport-version": "1.0",
        "x-message-id": message_id,
        "x-correlation-id": correlation_id,
        "x-attempt": attempt,
        "x-max-attempts": max_attempts,
    }
    if attempt > 1:
        headers["x-retry-reason"] = "TRANSIENT_PROVIDER_ERROR"
        headers["x-original-routing-key"] = "openstack.connection.validate"
    return headers


def fake_declared_topology() -> DeclaredTopology:
    from ops.messaging.topology import DeclaredTopology

    return DeclaredTopology(
        command_exchange=FakeExchange(name="cmp.cloud.command.v1"),
        event_exchange=FakeExchange(name="cmp.cloud.event.v1"),
        retry_exchange=FakeExchange(name="cmp.cloud.retry.v1"),
        dlx_exchange=FakeExchange(name="cmp.cloud.dlx.v1"),
        command_queue=FakeQueue(name="ops.command.v1"),
        retry_queues=(
            FakeQueue(name="ops.command.retry.1.v1"),
            FakeQueue(name="ops.command.retry.2.v1"),
        ),
        dlq_queue=FakeQueue(name="ops.command.dlq.v1"),
    )


def broker_noise_headers() -> dict[str, Any]:
    return {
        "x-death": [
            {
                "queue": "ops.command.retry.1.v1",
                "reason": "expired",
                "exchange": "cmp.cloud.retry.v1",
                "routing-keys": ["ops.command.retry.1"],
                "count": 1,
            }
        ],
        "x-delivery-count": 1,
    }
