from __future__ import annotations

from types import SimpleNamespace

from ops.openstack.scope import discover_effective_scope


def _connection(auth: object, *, project_id: str | None = None) -> SimpleNamespace:
    session = SimpleNamespace(auth=auth, get_project_id=lambda _auth: project_id)
    return SimpleNamespace(session=session)


def test_scope_discovery_reports_system_scope_and_identity_operations() -> None:
    result = discover_effective_scope(_connection(SimpleNamespace(system_scope="all")))
    assert result["scope_kind"] == "SYSTEM"
    assert result["capabilities"]["identity.domain.create"]["supported"] is True
    assert "token" not in repr(result).lower()


def test_scope_discovery_reads_system_scope_from_authorized_auth_ref() -> None:
    auth = SimpleNamespace(auth_ref=SimpleNamespace(system={"all": True}))
    result = discover_effective_scope(_connection(auth))
    assert result["scope_kind"] == "SYSTEM"
    assert result["scope_id"] == "all"


def test_scope_discovery_reports_project_scope_without_infering_admin() -> None:
    result = discover_effective_scope(
        _connection(
            SimpleNamespace(username="admin", project_id="project-1"), project_id="project-1"
        )
    )
    assert result["scope_kind"] == "PROJECT"
    assert result["capabilities"]["identity.domain.list"]["supported"] is False
    assert result["capabilities"]["identity.project.list"]["supported"] is True


def test_scope_discovery_reports_unknown_when_auth_plugin_is_unavailable() -> None:
    result = discover_effective_scope(SimpleNamespace(session=SimpleNamespace(auth=None)))
    assert result["scope_kind"] == "UNKNOWN"
    assert result["reason"] == "AUTH_PLUGIN_UNAVAILABLE"
