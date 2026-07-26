"""Effective Keystone scope discovery using public OpenStackSDK APIs.

The adapter deliberately reports what the authenticated session is scoped to;
it never treats a username, connection label, or configured name as evidence
of administrative authority.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ScopeKind(StrEnum):
    SYSTEM = "SYSTEM"
    DOMAIN = "DOMAIN"
    PROJECT = "PROJECT"
    UNKNOWN = "UNKNOWN"


class ScopeDiscoveryError(RuntimeError):
    """The SDK session could not expose a reliable effective scope."""


def _auth_plugin(connection: Any) -> Any | None:
    session = getattr(connection, "session", None)
    auth = getattr(session, "auth", None)
    return auth


def _session_project_id(connection: Any, auth: Any) -> str | None:
    session = getattr(connection, "session", None)
    getter = getattr(session, "get_project_id", None)
    if callable(getter):
        try:
            value = getter(auth)
        except Exception:
            value = None
        if value:
            return str(value)
    getter = getattr(getattr(connection, "identity", None), "get_project_id", None)
    if callable(getter):
        try:
            value = getter(auth)
        except Exception:
            value = None
        if value:
            return str(value)
    value = getattr(auth, "project_id", None)
    return str(value) if value else None


def discover_effective_scope(connection: Any) -> dict[str, Any]:
    """Return a safe, primitive-only effective Keystone scope document.

    ``system_scope``/``domain_id``/``project_id`` are read from the SDK auth
    plugin and the session's public helpers. No token, catalog, or credential
    material is inspected or returned.
    """

    auth = _auth_plugin(connection)
    if auth is None:
        return {
            "scope_kind": ScopeKind.UNKNOWN.value,
            "scope_id": None,
            "domain_id": None,
            "domain_name": None,
            "project_id": None,
            "capabilities": {
                name: {"supported": False, "reason": "AUTH_PLUGIN_UNAVAILABLE"}
                for name in (
                    "identity.domain.list",
                    "identity.project.list",
                    "identity.domain.create",
                    "identity.project.create",
                )
            },
            "reason": "AUTH_PLUGIN_UNAVAILABLE",
        }
    system_scope = getattr(auth, "system_scope", None)
    auth_ref = getattr(auth, "auth_ref", None)
    if system_scope is None and auth_ref is not None:
        system = getattr(auth_ref, "system", None)
        if isinstance(system, dict) and system:
            system_scope = next(iter(system))
    domain_id = getattr(auth, "domain_id", None)
    domain_name = getattr(auth, "domain_name", None)
    project_id = _session_project_id(connection, auth)
    if auth_ref is not None:
        project = getattr(auth_ref, "project", None)
        domain = getattr(auth_ref, "domain", None)
        if project_id is None and isinstance(project, dict):
            project_id = str(project.get("id") or project.get("name") or "") or None
        if domain_id is None and isinstance(domain, dict):
            domain_id = domain.get("id")
        if domain_name is None and isinstance(domain, dict):
            domain_name = domain.get("name")

    if system_scope:
        kind = ScopeKind.SYSTEM
        scope_id = str(system_scope)
        reason = None
    elif project_id:
        kind = ScopeKind.PROJECT
        scope_id = project_id
        reason = None
    elif domain_id or domain_name:
        kind = ScopeKind.DOMAIN
        scope_id = str(domain_id or domain_name)
        reason = None
    else:
        kind = ScopeKind.UNKNOWN
        scope_id = None
        reason = "EFFECTIVE_SCOPE_UNAVAILABLE"

    capabilities = {
        "identity.domain.list": {
            "supported": kind is ScopeKind.SYSTEM,
            "reason": None if kind is ScopeKind.SYSTEM else "SYSTEM_SCOPE_REQUIRED",
        },
        "identity.project.list": {
            "supported": kind in {ScopeKind.SYSTEM, ScopeKind.DOMAIN, ScopeKind.PROJECT},
            "reason": None if kind is not ScopeKind.UNKNOWN else "EFFECTIVE_SCOPE_UNAVAILABLE",
        },
        "identity.domain.create": {
            "supported": kind is ScopeKind.SYSTEM,
            "reason": None if kind is ScopeKind.SYSTEM else "SYSTEM_SCOPE_REQUIRED",
        },
        "identity.project.create": {
            "supported": kind in {ScopeKind.SYSTEM, ScopeKind.DOMAIN},
            "reason": None
            if kind in {ScopeKind.SYSTEM, ScopeKind.DOMAIN}
            else "DOMAIN_OR_SYSTEM_SCOPE_REQUIRED",
        },
    }
    return {
        "scope_kind": kind.value,
        "scope_id": scope_id,
        "domain_id": str(domain_id) if domain_id else None,
        "domain_name": str(domain_name) if domain_name else None,
        "project_id": project_id,
        "capabilities": capabilities,
        "reason": reason,
    }


# Short alias used by adapter callers that already have a connection-scoped
# operation context.
discover_scope = discover_effective_scope
