from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import ops.application.handlers.instance_create as handler_module
from ops.application.handlers.instance_create import (
    _create_kwargs,
    _find_server_by_operation,
    _resolve_floating_ip_port,
    _resolve_security_groups_for_nova,
    instance_create,
)
from ops.config import Settings
from ops.contracts.messages.envelope import MessageEnvelope
from ops.contracts.messages.instance import InstanceCommandPayload
from ops.contracts.messages.types import OPERATION_FAILED
from ops.messaging.consumer import HandlerFailedResult, HandlerSuccess


def _payload(**overrides: object) -> InstanceCommandPayload:
    create = {
        "name": "demo",
        "flavor_provider_resource_id": "flavor-1",
        "boot_source": "IMAGE",
        "image_provider_resource_id": "image-1",
        "network_provider_resource_ids": ["network-1"],
    }
    create.update(overrides)
    return InstanceCommandPayload.model_validate({"action": "CREATE", "create": create})


def test_create_kwargs_injects_operation_marker_and_managed_keypair() -> None:
    operation_id = uuid.uuid4()
    kwargs = _create_kwargs(
        _payload(ssh_public_key="ssh-ed25519 " + "A" * 40),
        operation_id,
        key_name=f"cmp-{operation_id}",
    )

    assert kwargs["key_name"] == f"cmp-{operation_id}"
    assert kwargs["metadata"]["cmp_operation_id"] == str(operation_id)
    assert kwargs["metadata"]["cmp_keypair_name"] == f"cmp-{operation_id}"
    assert kwargs["metadata"]["cmp_ssh_username"] == "ubuntu"


def test_create_kwargs_does_not_put_private_key_material_in_request() -> None:
    kwargs = _create_kwargs(_payload(), uuid.uuid4())

    assert "private_key" not in kwargs
    assert "ssh_public_key" not in kwargs


def test_create_kwargs_uses_resolved_security_group_names() -> None:
    kwargs = _create_kwargs(
        _payload(security_group_provider_resource_ids=["sg-uuid-1"]),
        uuid.uuid4(),
        security_groups=[{"name": "ttcntt-default-sg"}],
    )

    assert kwargs["security_groups"] == [{"name": "ttcntt-default-sg"}]


def test_create_kwargs_omits_security_groups_when_none_resolved() -> None:
    kwargs = _create_kwargs(_payload(), uuid.uuid4(), security_groups=[])

    assert "security_groups" not in kwargs


def test_resolve_security_groups_for_nova_maps_ids_to_names() -> None:
    connection = SimpleNamespace(
        network=SimpleNamespace(
            get_security_group=lambda sg_id: SimpleNamespace(id=sg_id, name="ttcntt-default-sg")
        )
    )

    assert _resolve_security_groups_for_nova(connection, ["sg-uuid-1"]) == [
        {"name": "ttcntt-default-sg"}
    ]


@pytest.mark.asyncio
async def test_find_server_uses_operation_metadata_not_display_name() -> None:
    operation_id = uuid.uuid4()
    expected = SimpleNamespace(metadata={"cmp_operation_id": str(operation_id)})

    class Compute:
        def find_server(self, name: str, *, ignore_missing: bool) -> object | None:
            assert name == "user-visible-name"
            assert ignore_missing is True
            return expected

    result = await _find_server_by_operation(
        Compute(), operation_id, server_name="user-visible-name", timeout_seconds=1
    )

    assert result is expected


def _instance_create_command() -> MessageEnvelope:
    return MessageEnvelope.model_validate(
        {
            "message_id": "11111111-1111-4111-8111-111111111111",
            "message_type": "openstack.instance.command",
            "schema_version": "1.0",
            "occurred_at": "2026-07-17T00:00:00Z",
            "correlation_id": "22222222-2222-4222-8222-222222222222",
            "operation_id": "33333333-3333-4333-8333-333333333333",
            "provider_id": "44444444-4444-4444-8444-444444444444",
            "provider_connection_id": "55555555-5555-4555-8555-555555555555",
            "trace_context": {},
            "payload": {
                "action": "CREATE",
                "create": {
                    "name": "demo",
                    "flavor_provider_resource_id": "flavor-1",
                    "boot_source": "IMAGE",
                    "image_provider_resource_id": "image-1",
                    "network_provider_resource_ids": ["network-1"],
                },
            },
        }
    )


@pytest.mark.asyncio
async def test_instance_create_maps_novalidhost_terminal_failure(monkeypatch) -> None:
    server_id = "server-novalidhost"
    error_server = SimpleNamespace(
        id=server_id,
        status="ERROR",
        request_ids=("req-nova-scheduler",),
        fault={
            "message": "NoValidHost",
            "code": 500,
            "details": "No valid host was found. traceback must not leak",
        },
        metadata={"cmp_operation_id": "33333333-3333-4333-8333-333333333333"},
        addresses={},
        name="demo",
    )

    class FakeCompute:
        def find_server(self, name: str, *, ignore_missing: bool) -> object | None:
            return None

        def create_server(self, **kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                id=server_id,
                status="BUILD",
                metadata=kwargs.get("metadata", {}),
                addresses={},
                name=kwargs.get("name", "demo"),
            )

        def get_server(self, server: str) -> SimpleNamespace:
            assert server == server_id
            return error_server

    class FakeResolver:
        def __init__(self, _settings: Settings) -> None:
            pass

        async def resolve(self, _connection_id: uuid.UUID) -> SimpleNamespace:
            return SimpleNamespace(
                auth_url="https://keystone.example/v3",
                username="ops-user",
                password="secret",  # pragma: allowlist secret
                user_domain_name="Default",
                project_name="demo",
                project_domain_name="Default",
                region_name="RegionOne",
                interface="public",
                verify_tls=True,
            )

    @contextmanager
    def fake_connection(_resolution: object, _settings: Settings):
        yield SimpleNamespace(compute=FakeCompute(), network=None)

    monkeypatch.setattr(handler_module, "CredentialResolver", FakeResolver)
    monkeypatch.setattr(handler_module, "openstack_connection", fake_connection)

    outcome = await instance_create(
        _instance_create_command(),
        None,
        "openstack.instance.command",
        settings=Settings(environment="test", _env_file=None, openstack_timeout_seconds=1),
    )

    assert isinstance(outcome, HandlerFailedResult)
    assert outcome.result_routing_key == OPERATION_FAILED
    failed = json.loads(outcome.result_body)
    error = failed["payload"]["error"]
    assert error["code"] == "INSUFFICIENT_CAPACITY"
    assert error["category"] == "QUOTA"
    assert error["retryable"] is False
    assert error["provider"] == "OPENSTACK"
    assert error["provider_service"] == "compute"
    assert error["provider_request_id"] == "req-nova-scheduler"
    assert error["details"]["provider_fault_code"] == "NoValidHost"
    assert "traceback must not leak" not in json.dumps(failed)
    assert "password" not in json.dumps(failed)


def _mapped_port(
    *,
    port_id: str = "port-1",
    network_id: str = "network-1",
    instance_id: str = "server-1",
    project_id: str = "p1",
) -> dict[str, object]:
    return {
        "provider_resource_id": port_id,
        "name": port_id,
        "attributes": {
            "network_id": network_id,
            "device_id": instance_id,
            "device_owner": "compute:nova",
        },
        "project_provider_resource_id": project_id,
    }


def _instance_create_command_with_fip(**create_overrides: object) -> MessageEnvelope:
    create = {
        "name": "demo",
        "flavor_provider_resource_id": "flavor-1",
        "boot_source": "IMAGE",
        "image_provider_resource_id": "image-1",
        "network_provider_resource_ids": ["network-1"],
        "floating_network_provider_resource_id": "ext-net",
    }
    create.update(create_overrides)
    envelope = _instance_create_command()
    payload = dict(envelope.payload)
    payload["create"] = create
    return envelope.model_copy(update={"payload": payload})


def _fake_resolution() -> SimpleNamespace:
    return SimpleNamespace(
        auth_url="https://keystone.example/v3",
        username="ops-user",
        password="secret",  # pragma: allowlist secret
        user_domain_name="Default",
        project_name="demo",
        project_domain_name="Default",
        region_name="RegionOne",
        interface="public",
        verify_tls=True,
    )


def _active_server(server_id: str = "server-1") -> SimpleNamespace:
    return SimpleNamespace(
        id=server_id,
        status="ACTIVE",
        metadata={"cmp_operation_id": "33333333-3333-4333-8333-333333333333"},
        addresses={"network-1": [{"addr": "10.0.0.4", "version": 4}]},
        name="demo",
    )


class FakeResolver:
    def __init__(self, _settings: Settings) -> None:
        pass

    async def resolve(self, _connection_id: uuid.UUID) -> SimpleNamespace:
        return _fake_resolution()


def test_resolve_floating_ip_port_prefers_collected_ports() -> None:
    connection = SimpleNamespace(
        session=SimpleNamespace(auth=SimpleNamespace(project_id="p1")),
        network=SimpleNamespace(ports=lambda **_: iter([])),
    )
    request = _payload(network_provider_resource_ids=["network-1"]).create

    port_id = _resolve_floating_ip_port(
        connection,
        "server-1",
        request,
        collected_ports=[_mapped_port()],
    )

    assert port_id == "port-1"


def test_resolve_floating_ip_port_falls_back_to_neutron_ports() -> None:
    connection = SimpleNamespace(
        session=SimpleNamespace(auth=SimpleNamespace(project_id="p1")),
        network=SimpleNamespace(
            ports=lambda **_: iter(
                [
                    SimpleNamespace(
                        id="port-2",
                        network_id="network-1",
                        device_id="server-1",
                        device_owner="compute:nova",
                        project_id="p1",
                    )
                ]
            )
        ),
    )
    request = _payload(network_provider_resource_ids=["network-1"]).create

    port_id = _resolve_floating_ip_port(connection, "server-1", request, collected_ports=[])

    assert port_id == "port-2"


def test_resolve_floating_ip_port_rejects_wrong_network() -> None:
    connection = SimpleNamespace(
        session=SimpleNamespace(auth=SimpleNamespace(project_id="p1")),
        network=SimpleNamespace(ports=lambda **_: iter([])),
    )
    request = _payload(network_provider_resource_ids=["network-1"]).create

    with pytest.raises(ValueError, match="no suitable port found"):
        _resolve_floating_ip_port(
            connection,
            "server-1",
            request,
            collected_ports=[_mapped_port(network_id="other-network")],
        )


@pytest.mark.asyncio
async def test_instance_create_associates_floating_ip_via_neutron_port(monkeypatch) -> None:
    server = _active_server()
    network_updates: list[dict[str, object]] = []

    class FakeCompute:
        def find_server(self, name: str, *, ignore_missing: bool) -> object | None:
            return None

        def create_server(self, **kwargs: object) -> SimpleNamespace:
            return server

        def get_server(self, server_id: str) -> SimpleNamespace:
            assert server_id == server.id
            return server

    class FakeNetwork:
        def ips(self):
            return iter([])

        def create_ip(self, **kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                id="fip-1",
                floating_ip_address="203.0.113.10",
                floating_network_id=kwargs["floating_network_id"],
                port_id=None,
                description=kwargs["description"],
            )

        def update_ip(self, floating: object, **kwargs: object) -> SimpleNamespace:
            network_updates.append(kwargs)
            floating.port_id = kwargs["port_id"]
            return floating

    @contextmanager
    def fake_connection(_resolution: object, _settings: Settings):
        yield SimpleNamespace(
            compute=FakeCompute(),
            network=FakeNetwork(),
            session=SimpleNamespace(auth=SimpleNamespace(project_id="p1")),
        )

    def fake_collect(_connection: object, _instance_id: str):
        return ([_mapped_port(instance_id=server.id)], [])

    monkeypatch.setattr(handler_module, "CredentialResolver", FakeResolver)
    monkeypatch.setattr(handler_module, "openstack_connection", fake_connection)
    monkeypatch.setattr(handler_module, "collect_instance_relationships", fake_collect)

    outcome = await instance_create(
        _instance_create_command_with_fip(),
        None,
        "openstack.instance.command",
        settings=Settings(environment="test", _env_file=None, openstack_timeout_seconds=1),
    )

    assert isinstance(outcome, HandlerSuccess)
    assert network_updates == [{"port_id": "port-1"}]
    completed = json.loads(outcome.result_messages[-1][1])
    assert completed["payload"]["result"]["access"]["ssh"]["host"] == "203.0.113.10"
    assert completed["payload"]["result"]["access"]["ssh"]["floating_ip_id"] == "fip-1"


@pytest.mark.asyncio
async def test_instance_create_skips_floating_ip_association_when_already_associated(
    monkeypatch,
) -> None:
    server = _active_server()
    network_updates: list[dict[str, object]] = []

    class FakeNetwork:
        def ips(self):
            return iter(
                [
                    SimpleNamespace(
                        id="fip-1",
                        floating_ip_address="203.0.113.10",
                        floating_network_id="ext-net",
                        port_id="port-1",
                        description="cmp-operation-33333333-3333-4333-8333-333333333333",
                    )
                ]
            )

        def update_ip(self, floating: object, **kwargs: object) -> SimpleNamespace:
            network_updates.append(kwargs)
            return floating

    class FakeCompute:
        def find_server(self, name: str, *, ignore_missing: bool) -> object | None:
            return server

        def get_server(self, server_id: str) -> SimpleNamespace:
            return server

    @contextmanager
    def fake_connection(_resolution: object, _settings: Settings):
        yield SimpleNamespace(
            compute=FakeCompute(),
            network=FakeNetwork(),
            session=SimpleNamespace(auth=SimpleNamespace(project_id="p1")),
        )

    def fake_collect(_connection: object, _instance_id: str):
        return ([_mapped_port(instance_id=server.id)], [])

    monkeypatch.setattr(handler_module, "CredentialResolver", FakeResolver)
    monkeypatch.setattr(handler_module, "openstack_connection", fake_connection)
    monkeypatch.setattr(handler_module, "collect_instance_relationships", fake_collect)

    outcome = await instance_create(
        _instance_create_command_with_fip(),
        None,
        "openstack.instance.command",
        settings=Settings(environment="test", _env_file=None, openstack_timeout_seconds=1),
    )

    assert isinstance(outcome, HandlerSuccess)
    assert network_updates == []
    completed = json.loads(outcome.result_messages[-1][1])
    assert completed["payload"]["result"]["access"]["ssh"]["host"] == "203.0.113.10"


@pytest.mark.asyncio
async def test_instance_create_fails_when_no_suitable_port_for_floating_ip(monkeypatch) -> None:
    server = _active_server()

    class FakeNetwork:
        def ips(self):
            return iter([])

        def create_ip(self, **kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                id="fip-1",
                floating_ip_address="203.0.113.10",
                floating_network_id=kwargs["floating_network_id"],
                port_id=None,
                description=kwargs["description"],
            )

        def ports(self, **_: object):
            return iter([])

    class FakeCompute:
        def find_server(self, name: str, *, ignore_missing: bool) -> object | None:
            return None

        def create_server(self, **kwargs: object) -> SimpleNamespace:
            return server

        def get_server(self, server_id: str) -> SimpleNamespace:
            return server

    @contextmanager
    def fake_connection(_resolution: object, _settings: Settings):
        yield SimpleNamespace(
            compute=FakeCompute(),
            network=FakeNetwork(),
            session=SimpleNamespace(auth=SimpleNamespace(project_id="p1")),
        )

    def fake_collect(_connection: object, _instance_id: str):
        return ([], [])

    monkeypatch.setattr(handler_module, "CredentialResolver", FakeResolver)
    monkeypatch.setattr(handler_module, "openstack_connection", fake_connection)
    monkeypatch.setattr(handler_module, "collect_instance_relationships", fake_collect)

    outcome = await instance_create(
        _instance_create_command_with_fip(),
        None,
        "openstack.instance.command",
        settings=Settings(environment="test", _env_file=None, openstack_timeout_seconds=1),
    )

    assert isinstance(outcome, HandlerFailedResult)
    assert outcome.result_routing_key == OPERATION_FAILED


@pytest.mark.asyncio
async def test_instance_create_without_floating_ip_does_not_call_neutron_update(
    monkeypatch,
) -> None:
    server = _active_server()
    network_updates: list[dict[str, object]] = []

    class FakeNetwork:
        def update_ip(self, floating: object, **kwargs: object) -> SimpleNamespace:
            network_updates.append(kwargs)
            return floating

    class FakeCompute:
        def find_server(self, name: str, *, ignore_missing: bool) -> object | None:
            return None

        def create_server(self, **kwargs: object) -> SimpleNamespace:
            return server

        def get_server(self, server_id: str) -> SimpleNamespace:
            return server

    @contextmanager
    def fake_connection(_resolution: object, _settings: Settings):
        yield SimpleNamespace(
            compute=FakeCompute(),
            network=FakeNetwork(),
            session=SimpleNamespace(auth=SimpleNamespace(project_id="p1")),
        )

    def fake_collect(_connection: object, _instance_id: str):
        return ([_mapped_port(instance_id=server.id)], [])

    monkeypatch.setattr(handler_module, "CredentialResolver", FakeResolver)
    monkeypatch.setattr(handler_module, "openstack_connection", fake_connection)
    monkeypatch.setattr(handler_module, "collect_instance_relationships", fake_collect)

    outcome = await instance_create(
        _instance_create_command(),
        None,
        "openstack.instance.command",
        settings=Settings(environment="test", _env_file=None, openstack_timeout_seconds=1),
    )

    assert isinstance(outcome, HandlerSuccess)
    assert network_updates == []
    completed = json.loads(outcome.result_messages[-1][1])
    assert completed["payload"]["result"]["access"]["ssh"]["host"] == "10.0.0.4"
    assert completed["payload"]["result"]["access"]["ssh"]["floating_ip_id"] is None
