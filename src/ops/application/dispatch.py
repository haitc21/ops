"""Validate and dispatch typed command handlers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ops.application.handlers.connection_validate import make_connection_validate
from ops.application.handlers.instance_action import make_instance_action
from ops.application.handlers.instance_create import make_instance_create
from ops.application.handlers.inventory_collect import (
    make_inventory_collect,
    make_inventory_refresh,
)
from ops.application.handlers.registry import HandlerRegistry
from ops.application.handlers.resource_operations import make_resource_operation
from ops.application.handlers.stub_connection_validate import make_stub_connection_validate
from ops.application.validation import EnvelopeReject, validate_command_envelope
from ops.config import Settings
from ops.contracts.messages.delivery import DeliveryMetadata
from ops.contracts.messages.instance import InstanceAction
from ops.contracts.messages.types import (
    CONNECTION_VALIDATE,
    FLOATING_IP_ALLOCATE,
    FLOATING_IP_ASSOCIATE,
    FLOATING_IP_DISASSOCIATE,
    FLOATING_IP_RELEASE,
    IDENTITY_DOMAIN_CREATE,
    IDENTITY_DOMAIN_DELETE,
    IDENTITY_DOMAIN_UPDATE,
    IDENTITY_PROJECT_CREATE,
    IDENTITY_PROJECT_DELETE,
    IDENTITY_PROJECT_UPDATE,
    IDENTITY_ROLE_COLLECT,
    IDENTITY_ROLE_ENSURE,
    IDENTITY_ROLE_REVOKE,
    INSTANCE_CREATE,
    INSTANCE_DELETE,
    INSTANCE_GET,
    INSTANCE_REBOOT,
    INSTANCE_START,
    INSTANCE_STOP,
    INVENTORY_COLLECT,
    INVENTORY_REFRESH,
    NETWORK_CREATE,
    NETWORK_DELETE,
    NETWORK_UPDATE,
    PORT_CREATE,
    PORT_DELETE,
    PORT_UPDATE,
    QUOTA_COLLECT,
    QUOTA_UPDATE,
    ROUTER_CREATE,
    ROUTER_DELETE,
    ROUTER_INTERFACE_ENSURE,
    ROUTER_INTERFACE_REMOVE,
    ROUTER_UPDATE,
    SECURITY_GROUP_CREATE,
    SECURITY_GROUP_DELETE,
    SECURITY_GROUP_RULE_CREATE,
    SECURITY_GROUP_RULE_DELETE,
    SECURITY_GROUP_UPDATE,
    SNAPSHOT_CREATE,
    SNAPSHOT_DELETE,
    SNAPSHOT_UPDATE,
    SUBNET_CREATE,
    SUBNET_DELETE,
    SUBNET_UPDATE,
    VOLUME_ATTACH,
    VOLUME_CREATE,
    VOLUME_DELETE,
    VOLUME_DETACH,
    VOLUME_RESIZE,
)
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
    registry.register(INVENTORY_COLLECT, make_inventory_collect(settings))
    registry.register(INVENTORY_REFRESH, make_inventory_refresh(settings))
    registry.register(INSTANCE_CREATE, make_instance_create(settings))
    registry.register(INSTANCE_GET, make_instance_action(settings, InstanceAction.GET))
    registry.register(INSTANCE_START, make_instance_action(settings, InstanceAction.START))
    registry.register(INSTANCE_STOP, make_instance_action(settings, InstanceAction.STOP))
    registry.register(INSTANCE_REBOOT, make_instance_action(settings, InstanceAction.REBOOT))
    registry.register(INSTANCE_DELETE, make_instance_action(settings, InstanceAction.DELETE))
    identity_handler = make_resource_operation(settings)
    for message_type in (
        IDENTITY_DOMAIN_CREATE,
        IDENTITY_DOMAIN_UPDATE,
        IDENTITY_DOMAIN_DELETE,
        IDENTITY_PROJECT_CREATE,
        IDENTITY_PROJECT_UPDATE,
        IDENTITY_PROJECT_DELETE,
        IDENTITY_ROLE_ENSURE,
        IDENTITY_ROLE_REVOKE,
        IDENTITY_ROLE_COLLECT,
        QUOTA_COLLECT,
        QUOTA_UPDATE,
        NETWORK_CREATE,
        NETWORK_UPDATE,
        NETWORK_DELETE,
        SUBNET_CREATE,
        SUBNET_UPDATE,
        SUBNET_DELETE,
        ROUTER_CREATE,
        ROUTER_UPDATE,
        ROUTER_DELETE,
        ROUTER_INTERFACE_ENSURE,
        ROUTER_INTERFACE_REMOVE,
        PORT_CREATE,
        PORT_UPDATE,
        PORT_DELETE,
        SECURITY_GROUP_CREATE,
        SECURITY_GROUP_UPDATE,
        SECURITY_GROUP_DELETE,
        SECURITY_GROUP_RULE_CREATE,
        SECURITY_GROUP_RULE_DELETE,
        FLOATING_IP_ALLOCATE,
        FLOATING_IP_ASSOCIATE,
        FLOATING_IP_DISASSOCIATE,
        FLOATING_IP_RELEASE,
        VOLUME_CREATE,
        VOLUME_RESIZE,
        VOLUME_DELETE,
        VOLUME_ATTACH,
        VOLUME_DETACH,
        SNAPSHOT_CREATE,
        SNAPSHOT_UPDATE,
        SNAPSHOT_DELETE,
    ):
        registry.register(message_type, identity_handler)
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
