"""Failure-path tests for the disposable RabbitMQ vhost harness."""

from __future__ import annotations

import pytest

from tests.integration.messaging import disposable_vhost


@pytest.mark.asyncio
async def test_setup_failure_after_create_deletes_owned_vhost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delete_urls: list[str] = []

    class Response:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError("management request failed")

    class Client:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return self

        async def __aexit__(self, *_args):  # type: ignore[no-untyped-def]
            return None

        async def put(self, url: str, **_kwargs):  # type: ignore[no-untyped-def]
            if "/api/permissions/" in url:
                return Response(500)
            return Response(201)

        async def get(self, url: str, **_kwargs):  # type: ignore[no-untyped-def]
            return Response(404)

        async def delete(self, url: str, **_kwargs):  # type: ignore[no-untyped-def]
            delete_urls.append(url)
            return Response(204)

    monkeypatch.setattr(disposable_vhost.httpx, "AsyncClient", lambda **_kwargs: Client())
    manager = disposable_vhost.DisposableVhostManager(
        base_amqp_url="amqp://user:password@127.0.0.1:5672/cmp",  # pragma: allowlist secret
        management_url="http://127.0.0.1:15672",
    )

    with pytest.raises(RuntimeError, match="management request failed"):
        await manager.setup()
    assert len(delete_urls) == 1


@pytest.mark.asyncio
async def test_setup_refuses_to_take_ownership_of_existing_vhost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    put_calls = 0
    delete_calls = 0

    class Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

    class Client:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return self

        async def __aexit__(self, *_args):  # type: ignore[no-untyped-def]
            return None

        async def get(self, url: str, **_kwargs):  # type: ignore[no-untyped-def]
            return Response()

        async def put(self, url: str, **_kwargs):  # type: ignore[no-untyped-def]
            nonlocal put_calls
            put_calls += 1
            return Response()

        async def delete(self, url: str, **_kwargs):  # type: ignore[no-untyped-def]
            nonlocal delete_calls
            delete_calls += 1
            return Response()

    monkeypatch.setattr(disposable_vhost.httpx, "AsyncClient", lambda **_kwargs: Client())
    monkeypatch.setenv("OPS_RABBITMQ_TEST_VHOST", "ops_test_existing")
    manager = disposable_vhost.DisposableVhostManager(
        base_amqp_url="amqp://user:password@127.0.0.1:5672/cmp",  # pragma: allowlist secret
        management_url="http://127.0.0.1:15672",
    )

    with pytest.raises(RuntimeError, match="already exists"):
        await manager.setup()
    assert put_calls == 0
    assert delete_calls == 0


@pytest.mark.asyncio
async def test_setup_reports_compensating_delete_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError("raw management failure")

    class Client:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return self

        async def __aexit__(self, *_args):  # type: ignore[no-untyped-def]
            return None

        async def get(self, url: str, **_kwargs):  # type: ignore[no-untyped-def]
            return Response(404)

        async def put(self, url: str, **_kwargs):  # type: ignore[no-untyped-def]
            return Response(500 if "/api/permissions/" in url else 201)

        async def delete(self, url: str, **_kwargs):  # type: ignore[no-untyped-def]
            return Response(500)

    monkeypatch.setattr(disposable_vhost.httpx, "AsyncClient", lambda **_kwargs: Client())
    manager = disposable_vhost.DisposableVhostManager(
        base_amqp_url="amqp://user:password@127.0.0.1:5672/cmp",  # pragma: allowlist secret
        management_url="http://127.0.0.1:15672",
    )

    with pytest.raises(RuntimeError, match="setup failed and cleanup failed") as caught:
        await manager.setup()
    assert "raw management failure" not in str(caught.value)
