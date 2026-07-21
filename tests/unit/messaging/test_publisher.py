"""Unit tests for confirmed publisher."""

from __future__ import annotations

import pytest

from ops.messaging.publisher import ConfirmedPublisher, PublishConfirmError
from tests.unit.messaging.fakes import FakeExchange


@pytest.mark.asyncio
async def test_publish_confirm_error_is_secret_safe() -> None:
    publisher = ConfirmedPublisher()

    class FailingExchange(FakeExchange):
        async def publish(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            import aio_pika

            raise aio_pika.exceptions.DeliveryError(None, None)

    with pytest.raises(PublishConfirmError, match="DeliveryError"):
        await publisher.publish(FailingExchange(), "ops.command.retry.1", b"{}")

    assert "amqp://" not in str(PublishConfirmError("DeliveryError"))


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError(), ConnectionError()])
async def test_publish_transport_failure_is_normalized(failure: Exception) -> None:
    class FailingExchange(FakeExchange):
        async def publish(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise failure

    with pytest.raises(PublishConfirmError):
        await ConfirmedPublisher().publish(FailingExchange(), "route", b"{}")


@pytest.mark.asyncio
async def test_publish_requires_positive_confirmation_and_persistent_message() -> None:
    import aio_pika

    observed: dict[str, object] = {}

    class NoConfirmExchange(FakeExchange):
        async def publish(self, message, *args, **kwargs):  # type: ignore[no-untyped-def]
            observed["delivery_mode"] = message.delivery_mode
            return None

    with pytest.raises(PublishConfirmError):
        await ConfirmedPublisher().publish(NoConfirmExchange(), "route", b"{}")
    assert observed["delivery_mode"] is aio_pika.DeliveryMode.PERSISTENT
