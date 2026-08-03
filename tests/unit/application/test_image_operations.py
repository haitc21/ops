"""Glance handler security and convergence coverage for OPS-1903."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ops.application.handlers.resource_operations import _assert_public_import_target, _execute
from ops.contracts.messages.resource_operations import ResourceOperationRequest, ScopeKind


class _Image:
    def __init__(self) -> None:
        self.images: dict[str, SimpleNamespace] = {}
        self.imports: list[dict[str, object]] = []

    def create_image(self, **kwargs):
        image = SimpleNamespace(id="img-1", status="queued", **kwargs)
        self.images[image.id] = image
        return image

    def get_image(self, image_id):
        return self.images[image_id]

    def import_image(self, image, *, method, uri):
        self.imports.append({"id": image.id, "method": method, "uri": uri})
        image.status = "active"
        return image

    def update_image(self, image, **kwargs):
        for key, value in kwargs.items():
            setattr(image, key, value)
        return image

    def add_member(self, image, member):
        image.members = sorted(set(getattr(image, "members", [])) | {member})

    def remove_member(self, image, member):
        image.members = [item for item in getattr(image, "members", []) if item != member]

    def delete_image(self, image, ignore_missing=True):
        self.images.pop(image.id, None)


def _request(operation: str, *, provider_id: str | None = None, **parameters):
    return ResourceOperationRequest(
        operation_id="018f1a6e-9d38-7b29-a430-1c808b778dfa",
        resource_type="image",
        operation=operation,
        required_scope=ScopeKind.SYSTEM,
        provider_connection_id="018f1a6e-9d38-7b29-a430-1c808b778dfb",
        provider_resource_id=provider_id,
        parameters=parameters,
    )


def test_image_import_and_metadata_members_and_delete_converge_without_bytes(monkeypatch) -> None:
    proxy = _Image()
    connection = SimpleNamespace(image=proxy)
    monkeypatch.setattr(
        "ops.application.handlers.resource_operations.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("93.184.216.34", 443))],
    )
    image, state = _execute(
        connection,
        _request(
            "import_url",
            name="cmp-s19-image",
            disk_format="qcow2",
            container_format="bare",
            source_url="https://images.example.test/a.qcow2",
            operation_marker="op-1",
        ),
    )
    assert state.value == "SUCCEEDED"
    assert proxy.imports == [
        {"id": "img-1", "method": "web-download", "uri": "https://images.example.test/a.qcow2"}
    ]
    _execute(
        connection,
        _request(
            "patch_metadata",
            provider_id="img-1",
            metadata={"architecture": "x86_64"},
            remove_metadata_keys=[],
        ),
    )
    _execute(
        connection, _request("grant_member", provider_id="img-1", member_project_id="project-1")
    )
    assert image.architecture == "x86_64" and image.members == ["project-1"]
    _execute(connection, _request("delete", provider_id="img-1"))
    assert proxy.images == {}


def test_image_handler_rejects_private_or_query_url_before_provider_mutation() -> None:
    proxy = _Image()
    with pytest.raises(ValueError):
        _execute(
            SimpleNamespace(image=proxy),
            _request(
                "import_url", name="bad", disk_format="qcow2", source_url="https://127.0.0.1/a?q=x"
            ),
        )
    assert proxy.images == {}


def test_import_dns_rejects_private_and_reserved_answers(monkeypatch) -> None:
    monkeypatch.setattr(
        "ops.application.handlers.resource_operations.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("10.0.0.8", 443))],
    )
    with pytest.raises(ValueError, match="non-public"):
        _assert_public_import_target("https://images.example.test/a.qcow2")
