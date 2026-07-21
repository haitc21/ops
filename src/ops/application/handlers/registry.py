"""Exact message_type to handler registry."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ops.contracts.messages.delivery import DeliveryMetadata
from ops.contracts.messages.envelope import MessageEnvelope
from ops.messaging.consumer import HandlerOutcome

TypedHandlerFn = Callable[
    [MessageEnvelope, DeliveryMetadata, str],
    Awaitable[HandlerOutcome],
]


class HandlerRegistry:
    """Immutable-by-convention registry mapping exact message types to handlers."""

    def __init__(self) -> None:
        self._handlers: dict[str, TypedHandlerFn] = {}
        self._frozen = False

    def register(self, message_type: str, handler: TypedHandlerFn) -> None:
        if self._frozen:
            msg = "handler registry is frozen"
            raise RuntimeError(msg)
        if not message_type:
            msg = "empty message type"
            raise ValueError(msg)
        if message_type in self._handlers:
            msg = f"duplicate handler registration: {message_type}"
            raise ValueError(msg)
        self._handlers[message_type] = handler

    def freeze(self) -> None:
        self._frozen = True

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    def lookup(self, message_type: str) -> TypedHandlerFn | None:
        return self._handlers.get(message_type)

    def __contains__(self, message_type: str) -> bool:
        return message_type in self._handlers
