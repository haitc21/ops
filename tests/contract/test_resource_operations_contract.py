from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from ops.contracts.messages.resource_operations import (
    ResourceOperationRequest,
    ResourceOperationResult,
    ResourceOperationState,
    ScopeKind,
)


def test_scoped_resource_operation_contract_round_trips_cps_shape() -> None:
    operation_id = uuid.uuid4()
    request = ResourceOperationRequest.model_validate(
        {
            "operation_id": str(operation_id),
            "resource_type": "identity_domain",
            "operation": "list",
            "required_scope": "SYSTEM",
            "provider_connection_id": str(uuid.uuid4()),
            "parameters": {"include_disabled": False},
        }
    )
    result = ResourceOperationResult.model_validate(
        {
            "operation_id": str(operation_id),
            "resource_type": request.resource_type,
            "operation": request.operation,
            "required_scope": request.required_scope,
            "provider_connection_id": str(request.provider_connection_id),
            "state": "SUCCEEDED",
            "resource": {"provider_resource_id": "domain-1", "name": "demo"},
        }
    )
    assert request.required_scope is ScopeKind.SYSTEM
    assert result.state is ResourceOperationState.SUCCEEDED


def test_scoped_resource_operation_rejects_unknown_major_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ResourceOperationRequest.model_validate(
            {
                "schema_version": "2.0",
                "operation_id": str(uuid.uuid4()),
                "resource_type": "project",
                "operation": "list",
                "required_scope": "PROJECT",
                "provider_connection_id": str(uuid.uuid4()),
                "unexpected": True,
            }
        )
