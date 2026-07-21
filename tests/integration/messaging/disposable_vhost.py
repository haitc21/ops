"""Disposable RabbitMQ vhost lifecycle for integration tests."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlparse, urlunparse

import httpx

FORBIDDEN_VHOSTS = frozenset({"cmp", "/"})


@dataclass(frozen=True, slots=True)
class AmqpEndpoint:
    scheme: str
    host: str
    port: int
    username: str
    password: str
    vhost: str


def parse_amqp_url(url: str) -> AmqpEndpoint:
    parsed = urlparse(url)
    if parsed.scheme not in {"amqp", "amqps"}:
        msg = "unsupported AMQP URL scheme"
        raise ValueError(msg)
    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    vhost = unquote(parsed.path.lstrip("/")) or "/"
    port = parsed.port or (5671 if parsed.scheme == "amqps" else 5672)
    return AmqpEndpoint(
        scheme=parsed.scheme,
        host=parsed.hostname or "127.0.0.1",
        port=port,
        username=username,
        password=password,
        vhost=vhost,
    )


def build_amqp_url(endpoint: AmqpEndpoint) -> str:
    user = quote(endpoint.username, safe="")
    password = quote(endpoint.password, safe="")
    vhost = quote(endpoint.vhost, safe="")
    netloc = f"{user}:{password}@{endpoint.host}:{endpoint.port}"
    return urlunparse((endpoint.scheme, netloc, f"/{vhost}", "", "", ""))


def validate_integration_vhost(vhost: str) -> None:
    if vhost in FORBIDDEN_VHOSTS:
        msg = f"forbidden integration vhost: {vhost}"
        raise RuntimeError(msg)
    prefix = os.getenv("OPS_RABBITMQ_TEST_VHOST_PREFIX", "ops_test_")
    if not vhost.startswith(prefix):
        msg = f"integration vhost must start with {prefix!r}"
        raise RuntimeError(msg)


class DisposableVhostManager:
    """Create and delete an isolated RabbitMQ vhost for messaging integration tests."""

    def __init__(
        self,
        *,
        base_amqp_url: str,
        management_url: str,
    ) -> None:
        self._base = parse_amqp_url(base_amqp_url)
        self._management_url = management_url.rstrip("/")
        worker = os.getenv("PYTEST_XDIST_WORKER", "master")
        override = os.getenv("OPS_RABBITMQ_TEST_VHOST")
        self._vhost = override or f"ops_test_{worker}_{secrets.token_hex(4)}"
        self._integration_url: str | None = None
        self._owned = False

    @property
    def integration_url(self) -> str:
        if self._integration_url is None:
            msg = "disposable vhost has not been created"
            raise RuntimeError(msg)
        return self._integration_url

    async def setup(self) -> str:
        validate_integration_vhost(self._vhost)
        auth = (self._base.username, self._base.password)
        vhost_encoded = quote(self._vhost, safe="")
        async with httpx.AsyncClient(timeout=10.0) as client:
            existing = await client.get(
                f"{self._management_url}/api/vhosts/{vhost_encoded}",
                auth=auth,
            )
            if existing.status_code == 200:
                msg = "refusing to reuse integration vhost that already exists"
                raise RuntimeError(msg)
            if existing.status_code != 404:
                existing.raise_for_status()
            response = await client.put(
                f"{self._management_url}/api/vhosts/{vhost_encoded}",
                auth=auth,
            )
            response.raise_for_status()
            self._owned = True
            permissions = {
                "configure": ".*",
                "write": ".*",
                "read": ".*",
            }
            user_encoded = quote(self._base.username, safe="")
            try:
                perm_response = await client.put(
                    f"{self._management_url}/api/permissions/{vhost_encoded}/{user_encoded}",
                    auth=auth,
                    json=permissions,
                )
                perm_response.raise_for_status()
            except Exception as setup_error:
                if self._owned:
                    try:
                        await self.teardown()
                    except Exception as cleanup_error:
                        msg = (
                            "integration vhost setup failed and cleanup failed "
                            f"({type(setup_error).__name__}; {type(cleanup_error).__name__})"
                        )
                        raise RuntimeError(msg) from None
                raise
        endpoint = AmqpEndpoint(
            scheme=self._base.scheme,
            host=self._base.host,
            port=self._base.port,
            username=self._base.username,
            password=self._base.password,
            vhost=self._vhost,
        )
        self._integration_url = build_amqp_url(endpoint)
        return self._integration_url

    async def teardown(self) -> None:
        if not self._owned:
            return
        auth = (self._base.username, self._base.password)
        vhost_encoded = quote(self._vhost, safe="")
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.delete(
                f"{self._management_url}/api/vhosts/{vhost_encoded}",
                auth=auth,
            )
            if response.status_code not in {204, 404}:
                response.raise_for_status()
        self._owned = False
        self._integration_url = None
