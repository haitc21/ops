"""OpenStackSDK connection construction with ephemeral CA handling."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from openstack import connection

from ops.config import Settings
from ops.contracts.validation import CredentialResolution


@contextmanager
def openstack_connection(
    resolution: CredentialResolution, settings: Settings
) -> Iterator[connection.Connection]:
    ca_path: str | None = None
    try:
        if resolution.ca_cert_pem:
            fd, ca_path = tempfile.mkstemp(prefix="cmp-ca-", suffix=".pem")
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as ca_file:
                ca_file.write(resolution.ca_cert_pem)
                ca_file.flush()
            verify: bool | str = ca_path
        else:
            verify = resolution.verify_tls
        connection_kwargs: dict[str, Any] = {
            "auth_url": resolution.auth_url,
            "username": resolution.username,
            "password": resolution.password,
            "user_domain_name": resolution.user_domain_name,
            "project_domain_name": resolution.project_domain_name,
            "region_name": resolution.region_name,
            "interface": resolution.interface,
            "verify": verify,
            "app_name": "CMP",
            "app_version": "0.1",
            "connect_retries": 0,
            "http_timeout": settings.openstack_timeout_seconds,
        }
        if resolution.scope_kind == "SYSTEM":
            connection_kwargs["system_scope"] = "all"
        else:
            connection_kwargs["project_name"] = resolution.project_name
        conn = connection.Connection(
            **connection_kwargs,
        )
        yield conn
    finally:
        if ca_path:
            try:
                os.remove(ca_path)
            except FileNotFoundError:
                pass
