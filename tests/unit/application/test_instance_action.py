"""OPS-602 delete convergence handler tests."""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from openstack import exceptions as os_exc

import ops.application.handlers.instance_action as handler_module
from ops.application.handlers.instance_action import instance_action
from ops.config import Settings
from ops.contracts.messages.delivery import DeliveryMetadata
from ops.contracts.messages.envelope import MessageEnvelope
from ops.contracts.messages.instance import InstanceAction
from ops.contracts.messages.types import OPERATION_COMPLETED, OPERATION_FAILED
from ops.messaging.consumer import HandlerFailedResult, HandlerSuccess


def _delete_command() -> MessageEnvelope:
    return MessageEnvelope.model_validate(
        {
            "message_id": "11111111-1111-4111-8111-111111111111",
            "message_type": "openstack.instance.delete",
            "schema_version": "1.0",
            "occurred_at": "2026-07-23T00:00:00Z",
            "correlation_id": "22222222-2222-4222-8222-222222222222",
            "operation_id": "33333333-3333-4333-8333-333333333333",
            "provider_id": "44444444-4444-4444-8444-444444444444",
            "provider_connection_id": "55555555-5555-4555-8555-555555555555",
            "payload": {
                "action": "DELETE",
                "instance_provider_resource_id": "server-1",
            },
        }
    )


def _action_command(action: InstanceAction, **payload: str) -> MessageEnvelope:
    value = action.value.lower()
    return MessageEnvelope.model_validate(
        {
            "message_id": "11111111-1111-4111-8111-111111111111",
            "message_type": f"openstack.instance.{value}",
            "schema_version": "1.0",
            "occurred_at": "2026-07-23T00:00:00Z",
            "correlation_id": "22222222-2222-4222-8222-222222222222",
            "operation_id": "33333333-3333-4333-8333-333333333333",
            "provider_id": "44444444-4444-4444-8444-444444444444",
            "provider_connection_id": "55555555-5555-4555-8555-555555555555",
            "payload": {
                "action": action.value,
                "instance_provider_resource_id": "server-1",
                **payload,
            },
        }
    )


def _retry_metadata(action: InstanceAction) -> DeliveryMetadata:
    return DeliveryMetadata.model_validate(
        {
            "x-transport-version": "1.0",
            "x-message-id": "11111111-1111-4111-8111-111111111111",
            "x-correlation-id": "22222222-2222-4222-8222-222222222222",
            "x-attempt": 2,
            "x-max-attempts": 3,
            "x-retry-reason": "PROVIDER_UNAVAILABLE",
            "x-original-routing-key": f"openstack.instance.{action.value.lower()}",
        }
    )


def _run_action(
    monkeypatch,
    action: InstanceAction,
    compute: object,
    *,
    metadata: DeliveryMetadata | None = None,
    **payload: str,
):
    class FakeResolver:
        def __init__(self, _settings):
            pass

        async def resolve(self, _connection_id):
            return SimpleNamespace()

    @contextmanager
    def fake_connection(_resolution, _settings):
        yield SimpleNamespace(compute=compute)

    monkeypatch.setattr(handler_module, "CredentialResolver", FakeResolver)
    monkeypatch.setattr(handler_module, "openstack_connection", fake_connection)
    return asyncio.run(
        instance_action(
            _action_command(action, **payload),
            metadata,
            f"openstack.instance.{action.value.lower()}",
            settings=Settings(environment="test", _env_file=None),
            expected_action=action,
        )
    )


def test_resize_waits_for_verify_resize(monkeypatch) -> None:
    calls: list[tuple[str, str | None]] = []
    responses = iter(
        [
            SimpleNamespace(id="server-1", name="demo", status="ACTIVE"),
            SimpleNamespace(id="server-1", name="demo", status="VERIFY_RESIZE"),
        ]
    )

    class Compute:
        def get_server(self, _resource_id):
            return next(responses)

        def resize_server(self, _server, flavor):
            calls.append(("resize", flavor))

    outcome = _run_action(
        monkeypatch,
        InstanceAction.RESIZE,
        Compute(),
        resize_flavor_provider_resource_id="flavor-2",
    )
    assert isinstance(outcome, HandlerSuccess)
    assert calls == [("resize", "flavor-2")]


def test_resize_retry_converges_without_reissuing_mutation(monkeypatch) -> None:
    calls: list[str] = []

    class Compute:
        def get_server(self, _resource_id):
            return SimpleNamespace(id="server-1", name="demo", status="VERIFY_RESIZE")

        def resize_server(self, _server, _flavor):
            calls.append("resize")

    outcome = _run_action(
        monkeypatch,
        InstanceAction.RESIZE,
        Compute(),
        metadata=_retry_metadata(InstanceAction.RESIZE),
        resize_flavor_provider_resource_id="flavor-2",
    )
    assert isinstance(outcome, HandlerSuccess)
    assert calls == []


def test_resize_invalid_state_returns_stable_conflict(monkeypatch) -> None:
    class Compute:
        def get_server(self, _resource_id):
            return SimpleNamespace(id="server-1", name="demo", status="PAUSED")

    outcome = _run_action(
        monkeypatch,
        InstanceAction.RESIZE,
        Compute(),
        resize_flavor_provider_resource_id="flavor-2",
    )
    assert isinstance(outcome, HandlerFailedResult)
    assert outcome.result_routing_key == OPERATION_FAILED
    body = json.loads(outcome.result_body)
    assert body["payload"]["error"]["code"] == "INVALID_RESOURCE_STATE"
    assert body["payload"]["error"]["details"]["provider_status"] == "PAUSED"


@pytest.mark.parametrize(
    ("action", "method_name"),
    [
        (InstanceAction.CONFIRM_RESIZE, "confirm_server_resize"),
        (InstanceAction.REVERT_RESIZE, "revert_server_resize"),
    ],
)
def test_confirm_and_revert_resize_require_verify_resize(
    monkeypatch, action: InstanceAction, method_name: str
) -> None:
    calls: list[str] = []
    responses = iter(
        [
            SimpleNamespace(id="server-1", name="demo", status="VERIFY_RESIZE"),
            SimpleNamespace(id="server-1", name="demo", status="ACTIVE"),
        ]
    )

    class Compute:
        def get_server(self, _resource_id):
            return next(responses)

    setattr(Compute, method_name, lambda self, _server: calls.append(method_name))
    outcome = _run_action(monkeypatch, action, Compute())
    assert isinstance(outcome, HandlerSuccess)
    assert calls == [method_name]


@pytest.mark.parametrize(
    "action",
    [InstanceAction.CONFIRM_RESIZE, InstanceAction.REVERT_RESIZE],
)
def test_resize_decision_retry_accepts_already_converged_active(
    monkeypatch, action: InstanceAction
) -> None:
    calls: list[str] = []

    class Compute:
        def get_server(self, _resource_id):
            return SimpleNamespace(id="server-1", name="demo", status="ACTIVE")

        def confirm_server_resize(self, _server):
            calls.append("confirm")

        def revert_server_resize(self, _server):
            calls.append("revert")

    outcome = _run_action(
        monkeypatch,
        action,
        Compute(),
        metadata=_retry_metadata(action),
    )
    assert isinstance(outcome, HandlerSuccess)
    assert calls == []


def test_rebuild_retry_detects_converged_image(monkeypatch) -> None:
    calls: list[str] = []

    class Compute:
        def get_server(self, _resource_id):
            return SimpleNamespace(
                id="server-1",
                name="demo",
                status="ACTIVE",
                image={"id": "image-2"},
            )

        def rebuild_server(self, _server, *, image):
            calls.append(image)

    outcome = _run_action(
        monkeypatch,
        InstanceAction.REBUILD,
        Compute(),
        metadata=_retry_metadata(InstanceAction.REBUILD),
        rebuild_image_provider_resource_id="image-2",
    )
    assert isinstance(outcome, HandlerSuccess)
    assert calls == []


def test_delete_emits_terminal_result_only_after_provider_absence(monkeypatch) -> None:
    calls: list[str] = []
    responses = iter(
        [
            SimpleNamespace(id="server-1", name="demo", status="ACTIVE"),
            os_exc.ResourceNotFound(),
        ]
    )

    class Compute:
        def get_server(self, _resource_id):
            response = next(responses)
            if isinstance(response, BaseException):
                raise response
            calls.append("get")
            return response

        def delete_server(self, _server):
            calls.append("delete")

    class FakeResolver:
        def __init__(self, _settings):
            pass

        async def resolve(self, _connection_id):
            return SimpleNamespace()

    @contextmanager
    def fake_connection(_resolution, _settings):
        yield SimpleNamespace(compute=Compute())

    monkeypatch.setattr(handler_module, "CredentialResolver", FakeResolver)
    monkeypatch.setattr(handler_module, "openstack_connection", fake_connection)

    outcome = asyncio.run(
        instance_action(
            _delete_command(),
            None,
            "openstack.instance.delete",
            settings=Settings(environment="test", _env_file=None),
            expected_action=InstanceAction.DELETE,
        )
    )

    assert isinstance(outcome, HandlerSuccess)
    assert calls == ["get", "delete"]
    assert [routing_key for routing_key, _body in outcome.result_messages] == [
        "cloud.operation.progress",
        OPERATION_COMPLETED,
    ]


def test_delete_is_idempotent_when_provider_already_absent(monkeypatch) -> None:
    calls: list[str] = []

    class Compute:
        def get_server(self, _resource_id):
            calls.append("get")
            raise os_exc.ResourceNotFound()

        def delete_server(self, _server):
            calls.append("delete")

    class FakeResolver:
        def __init__(self, _settings):
            pass

        async def resolve(self, _connection_id):
            return SimpleNamespace()

    @contextmanager
    def fake_connection(_resolution, _settings):
        yield SimpleNamespace(compute=Compute())

    monkeypatch.setattr(handler_module, "CredentialResolver", FakeResolver)
    monkeypatch.setattr(handler_module, "openstack_connection", fake_connection)

    outcome = asyncio.run(
        instance_action(
            _delete_command(),
            None,
            "openstack.instance.delete",
            settings=Settings(environment="test", _env_file=None),
            expected_action=InstanceAction.DELETE,
        )
    )

    assert isinstance(outcome, HandlerSuccess)
    assert calls == ["get"]
