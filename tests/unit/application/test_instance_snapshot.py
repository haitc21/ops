"""CPS/OPS-1904 Nova snapshot handler tests."""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from types import SimpleNamespace

import ops.application.handlers.instance_snapshot as handler_module
from ops.application.handlers.instance_snapshot import instance_snapshot
from ops.config import Settings
from ops.contracts.messages.envelope import MessageEnvelope
from ops.contracts.messages.types import OPERATION_COMPLETED
from ops.messaging.consumer import HandlerFailedResult, HandlerSuccess


def _command() -> MessageEnvelope:
    return MessageEnvelope.model_validate(
        {
            "message_id": "11111111-1111-4111-8111-111111111111",
            "message_type": "openstack.instance.snapshot.create",
            "schema_version": "1.0",
            "occurred_at": "2026-08-03T00:00:00Z",
            "correlation_id": "22222222-2222-4222-8222-222222222222",
            "operation_id": "33333333-3333-4333-8333-333333333333",
            "provider_id": "44444444-4444-4444-8444-444444444444",
            "provider_connection_id": "55555555-5555-4555-8555-555555555555",
            "payload": {
                "operation_id": "33333333-3333-4333-8333-333333333333",
                "provider_connection_id": "55555555-5555-4555-8555-555555555555",
                "instance_provider_resource_id": "server-1",
                "project_provider_resource_id": "project-1",
                "name": "before-upgrade",
                "metadata": {"purpose": "recovery"},
            },
        }
    )


def test_snapshot_creates_provider_image_without_bytes(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeResolver:
        def __init__(self, _settings):
            pass

        async def resolve(self, _connection_id):
            return SimpleNamespace()

    class Compute:
        def get_server(self, _server_id):
            return SimpleNamespace(id="server-1", status="ACTIVE", project_id="project-1")

        def create_server_image(self, _server, name, metadata, wait=False, timeout=120):
            calls.append({"name": name, "metadata": metadata, "wait": wait, "timeout": timeout})
            return SimpleNamespace(id="image-1", name=name, status="active", owner="project-1")

    class Image:
        def get_image(self, _image_id):
            return SimpleNamespace(
                id="image-1", name="before-upgrade", status="active", owner="project-1"
            )

        def images(self):
            return []

    @contextmanager
    def fake_connection(_resolution, _settings):
        yield SimpleNamespace(
            compute=Compute(), image=Image(), has_service=lambda name: name == "image"
        )

    monkeypatch.setattr(handler_module, "CredentialResolver", FakeResolver)
    monkeypatch.setattr(handler_module, "openstack_connection", fake_connection)
    outcome = asyncio.run(
        instance_snapshot(
            _command(),
            None,
            "openstack.instance.snapshot.create",
            settings=Settings(environment="test", _env_file=None),
        )
    )

    assert isinstance(outcome, HandlerSuccess)
    assert calls and "image_bytes" not in calls[0]["metadata"]
    payload = json.loads(outcome.result_messages[-1][1])["payload"]["result"]
    assert outcome.result_messages[-1][0] == OPERATION_COMPLETED
    assert payload["resource_type"] == "image"
    assert payload["resource"]["attributes"]["image_type"] == "snapshot"


def test_snapshot_replay_uses_existing_marker_and_rejects_foreign_owner(monkeypatch) -> None:
    calls: list[str] = []

    class FakeResolver:
        def __init__(self, _settings):
            pass

        async def resolve(self, _connection_id):
            return SimpleNamespace()

    class Compute:
        def get_server(self, _server_id):
            return SimpleNamespace(id="server-1", status="ACTIVE", project_id="project-1")

        def create_server_image(self, *_args, **_kwargs):
            calls.append("create")
            raise AssertionError("redelivery must not create a second image")

    class Image:
        def images(self):
            return [
                SimpleNamespace(
                    id="image-1",
                    name="before-upgrade",
                    status="active",
                    properties={"cmp_operation_id": "33333333-3333-4333-8333-333333333333"},
                )
            ]

        def get_image(self, _image_id):
            return self.images()[0]

    @contextmanager
    def fake_connection(_resolution, _settings):
        yield SimpleNamespace(compute=Compute(), image=Image(), has_service=lambda _name: True)

    monkeypatch.setattr(handler_module, "CredentialResolver", FakeResolver)
    monkeypatch.setattr(handler_module, "openstack_connection", fake_connection)
    outcome = asyncio.run(
        instance_snapshot(
            _command(),
            None,
            "openstack.instance.snapshot.create",
            settings=Settings(environment="test", _env_file=None),
        )
    )
    assert isinstance(outcome, HandlerSuccess)
    assert calls == []

    command = _command().model_copy(
        update={"payload": {**_command().payload, "project_provider_resource_id": "other-project"}}
    )
    outcome = asyncio.run(
        instance_snapshot(
            command,
            None,
            "openstack.instance.snapshot.create",
            settings=Settings(environment="test", _env_file=None),
        )
    )
    assert isinstance(outcome, HandlerFailedResult)
