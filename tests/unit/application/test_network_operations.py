from types import SimpleNamespace
from uuid import uuid4

import pytest
from openstack import exceptions as os_exc

from ops.application.handlers.resource_operations import _execute
from ops.contracts.messages.resource_operations import (
    ResourceOperationRequest,
    ResourceOperationState,
    ScopeKind,
)


class FakeNetwork:
    def __init__(self):
        self.items = []

    def networks(self, **_filters):
        return iter(self.items)

    def create_network(self, **kwargs):
        item = SimpleNamespace(id="net-1", name=kwargs["name"], project_id=kwargs.get("project_id"))
        self.items.append(item)
        return item

    def get_network(self, resource_id):
        return next(item for item in self.items if item.id == resource_id)

    def delete_network(self, item, ignore_missing=False):
        self.items.remove(item)


def request(resource_type: str, operation: str, parameters: dict, provider_resource_id=None):
    return ResourceOperationRequest(
        operation_id=uuid4(),
        resource_type=resource_type,
        operation=operation,
        required_scope=ScopeKind.PROJECT,
        provider_connection_id=uuid4(),
        provider_resource_id=provider_resource_id,
        parameters=parameters,
    )


def test_network_ensure_is_replay_safe():
    network = FakeNetwork()
    connection = SimpleNamespace(
        network=network, session=SimpleNamespace(auth=SimpleNamespace(project_id="p1"))
    )
    first, first_state = _execute(
        connection, request("network", "create", {"name": "internal", "project_id": "p1"})
    )
    second, second_state = _execute(
        connection, request("network", "ensure", {"name": "internal", "project_id": "p1"})
    )
    assert first.id == second.id
    assert first_state.value == second_state.value == "SUCCEEDED"


def test_network_rejects_cross_project_mutation():
    connection = SimpleNamespace(
        network=FakeNetwork(), session=SimpleNamespace(auth=SimpleNamespace(project_id="p1"))
    )
    with pytest.raises(ValueError, match="PROJECT_OWNERSHIP_MISMATCH"):
        _execute(connection, request("network", "create", {"name": "other", "project_id": "p2"}))


def test_security_rule_validates_ranges_before_provider_call():
    connection = SimpleNamespace(
        network=FakeNetwork(), session=SimpleNamespace(auth=SimpleNamespace(project_id="p1"))
    )
    with pytest.raises(ValueError, match="invalid port range"):
        _execute(
            connection,
            request(
                "security_group_rule",
                "create",
                {
                    "security_group_id": "sg-1",
                    "direction": "ingress",
                    "port_range_min": 9000,
                    "port_range_max": 22,
                },
            ),
        )


def test_network_guardrails_reject_external_mutation_and_bad_subnet_pool():
    connection = SimpleNamespace(
        network=FakeNetwork(), session=SimpleNamespace(auth=SimpleNamespace(project_id="p1"))
    )
    with pytest.raises(ValueError, match="administrator-only"):
        _execute(connection, request("network", "create", {"name": "public", "external": True}))
    with pytest.raises(ValueError, match="inside subnet cidr"):
        _execute(
            connection,
            request(
                "subnet",
                "create",
                {
                    "name": "bad",
                    "network_id": "net-1",
                    "cidr": "10.0.0.0/24",
                    "allocation_pools": [{"start": "10.0.1.10", "end": "10.0.1.20"}],
                },
            ),
        )


def test_security_rule_rejects_public_ingress_by_default():
    connection = SimpleNamespace(
        network=FakeNetwork(), session=SimpleNamespace(auth=SimpleNamespace(project_id="p1"))
    )
    with pytest.raises(ValueError, match="public ingress"):
        _execute(
            connection,
            request(
                "security_group_rule",
                "create",
                {
                    "security_group_id": "sg-1",
                    "direction": "ingress",
                    "remote_ip_prefix": "0.0.0.0/0",
                },
            ),
        )


class FakeFloatingIpNetwork:
    def __init__(self):
        self.updated = []

    def get_ip(self, resource_id):
        return SimpleNamespace(
            id=resource_id,
            floating_network_id="ext-net",
            port_id=None,
            project_id="p1",
        )

    def get_port(self, resource_id):
        return SimpleNamespace(id=resource_id, project_id="p1")

    def update_ip(self, existing, **kwargs):
        self.updated.append(kwargs)
        existing.port_id = kwargs.get("port_id")
        return existing


def test_floating_ip_associate_requires_port_id():
    connection = SimpleNamespace(
        network=FakeFloatingIpNetwork(),
        session=SimpleNamespace(auth=SimpleNamespace(project_id="p1")),
    )
    with pytest.raises(ValueError, match="port_id is required"):
        _execute(
            connection,
            request(
                "floating_ip",
                "associate",
                {},
                provider_resource_id="fip-1",
            ),
        )


def test_floating_ip_associate_updates_port():
    network = FakeFloatingIpNetwork()
    connection = SimpleNamespace(
        network=network,
        session=SimpleNamespace(auth=SimpleNamespace(project_id="p1")),
    )
    result, state = _execute(
        connection,
        request(
            "floating_ip",
            "associate",
            {"port_id": "port-1"},
            provider_resource_id="fip-1",
        ),
    )
    assert state.value == "SUCCEEDED"
    assert result.port_id == "port-1"
    assert network.updated == [{"port_id": "port-1"}]


class FakeFloatingIpNetworkAdminOwned(FakeFloatingIpNetwork):
    def get_ip(self, resource_id):
        return SimpleNamespace(
            id=resource_id,
            floating_network_id="ext-net",
            port_id=None,
            project_id="admin-project",
        )

    def get_port(self, resource_id):
        return SimpleNamespace(id=resource_id, project_id="p1")


def test_floating_ip_associate_checks_port_not_fip_project():
    network = FakeFloatingIpNetworkAdminOwned()
    connection = SimpleNamespace(
        network=network,
        session=SimpleNamespace(auth=SimpleNamespace(project_id="p1")),
    )
    result, state = _execute(
        connection,
        request(
            "floating_ip",
            "associate",
            {"port_id": "port-1"},
            provider_resource_id="fip-1",
        ),
    )
    assert state.value == "SUCCEEDED"
    assert network.updated == [{"port_id": "port-1"}]


class FakeFloatingIpNetworkGetIpNotFound(FakeFloatingIpNetwork):
    def __init__(self, fips):
        super().__init__()
        self.fips = fips
        self.get_ip_calls = 0

    def get_ip(self, resource_id):
        self.get_ip_calls += 1
        raise os_exc.NotFoundException()

    def ips(self):
        return iter(self.fips)


def test_floating_ip_associate_falls_back_to_exact_id_from_ips_list():
    network = FakeFloatingIpNetworkGetIpNotFound(
        [
            SimpleNamespace(
                id="fip-1",
                floating_network_id="ext-net",
                port_id=None,
                project_id="p1",
            )
        ]
    )
    connection = SimpleNamespace(
        network=network,
        session=SimpleNamespace(auth=SimpleNamespace(project_id="p1")),
    )
    result, state = _execute(
        connection,
        request(
            "floating_ip",
            "associate",
            {"port_id": "port-1"},
            provider_resource_id="fip-1",
        ),
    )
    assert network.get_ip_calls == 1
    assert state == ResourceOperationState.SUCCEEDED
    assert result.port_id == "port-1"
    assert network.updated == [{"port_id": "port-1"}]


def test_floating_ip_associate_rejects_when_exact_id_missing_from_ips_list():
    network = FakeFloatingIpNetworkGetIpNotFound(
        [
            SimpleNamespace(
                id="other-fip",
                floating_network_id="ext-net",
                port_id=None,
                project_id="p1",
            )
        ]
    )
    connection = SimpleNamespace(
        network=network,
        session=SimpleNamespace(auth=SimpleNamespace(project_id="p1")),
    )
    with pytest.raises(ValueError, match="floating IP not found"):
        _execute(
            connection,
            request(
                "floating_ip",
                "associate",
                {"port_id": "port-1"},
                provider_resource_id="fip-1",
            ),
        )
    assert network.get_ip_calls == 1
    assert network.updated == []


def test_floating_ip_release_is_already_absent_when_exact_id_missing_from_ips_list():
    network = FakeFloatingIpNetworkGetIpNotFound([])
    connection = SimpleNamespace(
        network=network,
        session=SimpleNamespace(auth=SimpleNamespace(project_id="p1")),
    )
    result, state = _execute(
        connection,
        request("floating_ip", "release", {}, provider_resource_id="fip-missing"),
    )
    assert result is None
    assert state == ResourceOperationState.ALREADY_ABSENT
    assert network.get_ip_calls == 1
