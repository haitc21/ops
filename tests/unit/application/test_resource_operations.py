from types import SimpleNamespace
from uuid import uuid4

import pytest
from openstack import exceptions as os_exc

from ops.application.handlers.resource_operations import _execute, _normalize_quota, _request
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

    def domains(self, **kwargs):
        return iter(()) if self.existing is None else iter((self.existing,))

    def create_domain(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(id="d1", name=kwargs["name"])

    def get_domain(self, value):
        if self.existing is None:
            raise RuntimeError(value)
        return self.existing


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
