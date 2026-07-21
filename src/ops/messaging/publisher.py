"""Publisher with broker confirms for OPS messaging."""

from __future__ import annotations

import logging
from typing import Any

import aio_pika
from aio_pika.abc import AbstractExchange

logger = logging.getLogger(__name__)


class PublishConfirmError(Exception):
    """Raised when the broker does not positively confirm a publish."""


class ConfirmedPublisher:
    """Publish messages and treat broker confirm failures as hard errors."""

    async def publish(
        self,
        exchange: AbstractExchange,
        routing_key: str,
        body: bytes,
        *,
        headers: dict[str, Any] | None = None,
        mandatory: bool = True,
        confirm_timeout: float | None = 10.0,
    ) -> None:
        message = aio_pika.Message(
            body=body,
            headers=headers or {},
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )
        try:
            confirmation = await exchange.publish(
                message,
                routing_key,
                mandatory=mandatory,
                timeout=confirm_timeout,
            )
        except (
            TimeoutError,
            aio_pika.exceptions.AMQPException,
            ConnectionError,
            OSError,
        ) as exc:
            logger.warning(
                "publish confirm failed",
                extra={
                    "routing_key": routing_key,
                    "error_type": type(exc).__name__,
                },
            )
            raise PublishConfirmError(type(exc).__name__) from None
        if confirmation is None:
            logger.warning(
                "publish did not return a positive confirmation",
                extra={"routing_key": routing_key},
            )
            raise PublishConfirmError("MissingConfirmation") from None
