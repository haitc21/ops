"""Replay-safe Nova flavor lifecycle handler coverage."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ops.application.handlers.resource_operations import _execute
from ops.contracts.messages.resource_operations import ResourceOperationRequest, ScopeKind


class _Compute:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []
        self.access = {"project-old"}
        self.specs = {"old": "value"}

    def flavors(self, **_kwargs):
        return []

    def create_flavor(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(id="flavor-new", **kwargs)

    def get_flavor(self, flavor_id):
        return SimpleNamespace(id=flavor_id, name="cmp-s19-small", ram=1024, vcpus=1, disk=10)

    def get_flavor_access(self, _flavor):
        return [SimpleNamespace(tenant_id=item) for item in self.access]

    def flavor_add_tenant_access(self, _flavor, tenant):
        self.access.add(tenant)

    def flavor_remove_tenant_access(self, _flavor, tenant):
        self.access.discard(tenant)

    def fetch_flavor_extra_specs(self, _flavor):
        return SimpleNamespace(extra_specs=dict(self.specs))

    def create_flavor_extra_specs(self, _flavor, *, extra_specs):
        self.specs.update(extra_specs)

    def delete_flavor_extra_specs_property(self, _flavor, prop):
        self.specs.pop(prop, None)


def _request(operation: str, *, provider_id: str | None = None, **parameters):
    return ResourceOperationRequest(
        operation_id="018f1a6e-9d38-7b29-a430-1c808b778dfa",
        resource_type="flavor",
        operation=operation,
        required_scope=ScopeKind.SYSTEM,
        provider_connection_id="018f1a6e-9d38-7b29-a430-1c808b778dfb",
        provider_resource_id=provider_id,
        parameters=parameters,
    )


def test_flavor_create_and_access_and_extra_specs_converge() -> None:
    compute = _Compute()
    connection = SimpleNamespace(compute=compute)

    resource, state = _execute(
        connection,
        _request(
            "create",
            name="cmp-s19-small",
            vcpus=1,
            ram_mib=1024,
            disk_gib=10,
            ephemeral_gib=0,
            swap_mib=0,
            is_public=False,
        ),
    )
    assert state.value == "SUCCEEDED"
    assert resource.id == "flavor-new"
    assert compute.created == [
        {
            "name": "cmp-s19-small",
            "vcpus": 1,
            "ram": 1024,
            "disk": 10,
            "ephemeral": 0,
            "swap": 0,
            "is_public": False,
        }
    ]

    _execute(
        connection,
        _request("replace_access", provider_id="flavor-new", access_project_ids=["project-new"]),
    )
    assert compute.access == {"project-new"}
    _execute(
        connection,
        _request(
            "patch_extra_specs",
            provider_id="flavor-new",
            extra_specs={"new": "value"},
            remove_extra_spec_keys=["old"],
        ),
    )
    assert compute.specs == {"new": "value"}


def test_flavor_create_rejects_conflicting_existing_shape() -> None:
    compute = _Compute()
    compute.flavors = lambda **_kwargs: [
        SimpleNamespace(id="other", name="cmp-s19-small", ram=2048, vcpus=1, disk=10)
    ]
    with pytest.raises(Exception, match="different shape"):
        _execute(
            SimpleNamespace(compute=compute),
            _request("create", name="cmp-s19-small", vcpus=1, ram_mib=1024, disk_gib=10),
        )
