from types import SimpleNamespace
from uuid import uuid4

import pytest

from ops.application.handlers.resource_operations import _execute
from ops.contracts.messages.resource_operations import ResourceOperationRequest, ScopeKind


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
