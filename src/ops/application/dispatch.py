"""Validate and dispatch typed command handlers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ops.application.handlers.connection_validate import make_connection_validate
from ops.application.handlers.registry import HandlerRegistry
from ops.application.handlers.stub_connection_validate import make_stub_connection_validate
from ops.application.validation import EnvelopeReject, validate_command_envelope
from ops.config import Settings
from ops.contracts.messages.delivery import DeliveryMetadata
from ops.contracts.messages.types import CONNECTION_VALIDATE
from ops.messaging.consumer import HandlerFn, HandlerNonRetryableError, HandlerOutcome


def build_default_registry(
    *,
    on_handler_call: Callable[[], None] | None = None,
    freeze: bool = False,
) -> HandlerRegistry:
    """Build a registry for tests or production.

    Pass ``freeze=True`` for production-like immutability; test instrumentation
    may use ``freeze=False`` to register additional handlers after build.
    """
    registry = HandlerRegistry()
    registry.register(CONNECTION_VALIDATE, make_stub_connection_validate(on_call=on_handler_call))
    if freeze:
        registry.freeze()
    return registry


def build_production_registry(settings: Settings) -> HandlerRegistry:
    registry = HandlerRegistry()
    registry.register(CONNECTION_VALIDATE, make_connection_validate(settings))
    registry.freeze()
    return registry


_DEFAULT_REGISTRY = build_default_registry(freeze=True)


async def dispatch_command(
    envelope: dict[str, Any],
    metadata: DeliveryMetadata,
    routing_key: str,
    *,
    registry: HandlerRegistry | None = None,
) -> HandlerOutcome:
    active_registry = registry or _DEFAULT_REGISTRY
    try:
        validated = validate_command_envelope(envelope)
    except EnvelopeReject:
        return HandlerNonRetryableError()

    handler = active_registry.lookup(validated.message_type)
    if handler is None:
        return HandlerNonRetryableError()

    return await handler(validated, metadata, routing_key)


def build_dispatch_handler(
    registry: HandlerRegistry | None = None,
) -> HandlerFn:
    active_registry = registry or _DEFAULT_REGISTRY

    async def handler(
        envelope: dict[str, Any],
        metadata: DeliveryMetadata,
        routing_key: str,
    ) -> HandlerOutcome:
        return await dispatch_command(
            envelope,
            metadata,
            routing_key,
            registry=active_registry,
        )

    return handler
