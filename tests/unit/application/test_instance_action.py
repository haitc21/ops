"""OPS-602 delete convergence handler tests."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

from openstack import exceptions as os_exc

import ops.application.handlers.instance_action as handler_module
from ops.application.handlers.instance_action import instance_action
from ops.config import Settings
from ops.contracts.messages.envelope import MessageEnvelope
from ops.contracts.messages.instance import InstanceAction
from ops.contracts.messages.types import OPERATION_COMPLETED
from ops.messaging.consumer import HandlerSuccess


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
