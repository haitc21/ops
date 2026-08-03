from uuid import uuid4

import pytest
from pydantic import ValidationError

from ops.contracts.messages.flavor_operations import FlavorOperationRequest


def test_pinned_flavor_contract_accepts_create_and_extra_spec_patch() -> None:
    create = FlavorOperationRequest(
        operation_id=uuid4(),
        provider_connection_id=uuid4(),
        operation="create",
        name="cmp-s19-small",
        vcpus=1,
        ram_mib=1024,
        disk_gib=10,
    )
    assert create.resource_type == "flavor"
    assert (
        FlavorOperationRequest(
            operation_id=uuid4(),
            provider_connection_id=uuid4(),
            operation="replace_access",
            provider_resource_id="flavor-1",
            access_project_ids=["project-1"],
        ).required_scope
        == "SYSTEM"
    )


def test_pinned_flavor_contract_rejects_delete_without_id() -> None:
    with pytest.raises(ValidationError):
        FlavorOperationRequest(
            operation_id=uuid4(), provider_connection_id=uuid4(), operation="delete"
        )
