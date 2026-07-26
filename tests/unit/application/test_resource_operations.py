from types import SimpleNamespace
from uuid import uuid4

import pytest
from openstack import exceptions as os_exc

from ops.application.handlers.resource_operations import (
    _execute,
    _normalize_quota,
    _request,
)
from ops.contracts.messages.envelope import MessageEnvelope
from ops.contracts.messages.resource_operations import (
    ResourceOperationRequest,
    ResourceOperationState,
    ScopeKind,
)


class Identity:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []
        self.existing = None
        self.updated: list[dict[str, object]] = []
        self.assignments: list[dict[str, object]] = []

    def domains(self, **kwargs):
        return iter(()) if self.existing is None else iter((self.existing,))

    def create_domain(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(id="d1", name=kwargs["name"])

    def projects(self, **kwargs):
        return iter(()) if self.existing is None else iter((self.existing,))

    def create_project(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(id="p1", name=kwargs["name"])

    def get_domain(self, value):
        if self.existing is None:
            raise RuntimeError(value)
        return self.existing

    def get_project(self, value):
        if self.existing is None:
            raise RuntimeError(value)
        return self.existing

    def update_domain(self, resource, **kwargs):
        self.updated.append(kwargs)
        return resource

    def find_user(self, value, ignore_missing=True):
        return SimpleNamespace(id="u1", name=value)

    def roles(self):
        return iter((SimpleNamespace(id="r1", name="admin"),))

    def role_assignments(self, **kwargs):
        return iter(self.assignments)

    def create_role_assignment(self, **kwargs):
        self.assignments.append(kwargs)
        return SimpleNamespace(**kwargs)


def _operation_request(
    resource_type: str,
    operation: str,
    parameters: dict[str, object],
    *,
    provider_resource_id: str | None = None,
):
    return ResourceOperationRequest(
        operation_id=uuid4(),
        resource_type=resource_type,
        operation=operation,
        required_scope=ScopeKind.SYSTEM,
        provider_connection_id=uuid4(),
        provider_resource_id=provider_resource_id,
        parameters=parameters,
    )


def test_domain_create_without_binding_rejects_name_collision() -> None:
    identity = Identity()
    identity.existing = SimpleNamespace(id="unbound", name="acme")
    with pytest.raises(os_exc.ConflictException, match="unbound object"):
        _execute(
            SimpleNamespace(identity=identity),
            _operation_request("domain", "create", {"name": "acme"}),
        )
    assert identity.created == []


def test_domain_create_with_provider_id_is_replay_safe() -> None:
    identity = Identity()
    identity.existing = SimpleNamespace(id="d1", name="acme")
    result, state = _execute(
        SimpleNamespace(identity=identity),
        _operation_request("domain", "create", {"name": "acme"}, provider_resource_id="d1"),
    )
    assert state is ResourceOperationState.SUCCEEDED
    assert result.id == "d1"
    assert identity.created == []


def test_domain_create_grants_creator_highest_role() -> None:
    identity = Identity()
    result, state = _execute(
        SimpleNamespace(identity=identity),
        _operation_request("domain", "create", {"name": "acme"}),
        "admin",
    )
    assert state is ResourceOperationState.SUCCEEDED
    assert result.id == "d1"
    assert identity.assignments == [{"role": "r1", "user": "u1", "domain": "d1"}]


def test_project_create_grants_creator_highest_role() -> None:
    identity = Identity()
    result, state = _execute(
        SimpleNamespace(identity=identity),
        _operation_request("project", "create", {"name": "cloud", "domain_id": "d1"}),
        "admin",
    )
    assert state is ResourceOperationState.SUCCEEDED
    assert result.id == "p1"
    assert identity.assignments == [{"role": "r1", "user": "u1", "project": "p1"}]


def test_domain_disable_updates_provider_resource_without_cmp_metadata() -> None:
    identity = Identity()
    identity.existing = SimpleNamespace(id="d1", name="acme")
    result, state = _execute(
        SimpleNamespace(identity=identity),
        _operation_request(
            "domain",
            "disable",
            {"binding_id": str(uuid4()), "org_id": "org-1"},
            provider_resource_id="d1",
        ),
    )
    assert state is ResourceOperationState.SUCCEEDED
    assert result.id == "d1"
    assert identity.updated == [{"enabled": False}]


def test_quota_unlimited_normalizes_to_none() -> None:
    quota = SimpleNamespace(to_dict=lambda: {"cores": -1, "ram": 2048, "_links": {}})
    assert _normalize_quota(quota) == {"cores": None, "ram": 2048}


def test_secret_parameters_are_rejected() -> None:
    envelope = MessageEnvelope.model_validate(
        {
            "message_id": uuid4(),
            "message_type": "openstack.identity.project.create",
            "schema_version": "1.0",
            "occurred_at": "2026-07-24T00:00:00Z",
            "correlation_id": uuid4(),
            "operation_id": uuid4(),
            "provider_id": uuid4(),
            "provider_connection_id": uuid4(),
            "payload": {
                "resource_type": "project",
                "operation": "create",
                "required_scope": "DOMAIN",
                "parameters": {"name": "safe", "password": "never"},  # pragma: allowlist secret
            },
        }
    )
    with pytest.raises(ValueError, match="secret parameters"):
        _request(envelope)
