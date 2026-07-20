"""OPS-003: health endpoint unit behavior."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from ops.config import Settings
from ops.main import create_app


class _FakeChecks:
    def __init__(self, rabbitmq_ok: bool = True) -> None:
        self.rabbitmq_ok = rabbitmq_ok

    async def check_rabbitmq(self) -> dict[str, Any]:
        return {"status": "up" if self.rabbitmq_ok else "down"}


def test_liveness_is_process_only() -> None:
    app = create_app(settings=Settings(environment="test", _env_file=None))
    response = TestClient(app).get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_ok_when_rabbitmq_up() -> None:
    app = create_app(settings=Settings(environment="test", _env_file=None))
    app.state.health_checks = _FakeChecks(rabbitmq_ok=True)
    response = TestClient(app).get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["rabbitmq"]["status"] == "up"
    assert "openstack" not in body["checks"]
    assert "database" not in body["checks"]


def test_readiness_fails_when_rabbitmq_down() -> None:
    app = create_app(settings=Settings(environment="test", _env_file=None))
    app.state.health_checks = _FakeChecks(rabbitmq_ok=False)
    response = TestClient(app).get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["rabbitmq"]["status"] == "down"


def test_readiness_ignores_openstack_outage_flag() -> None:
    settings = Settings(environment="test", _env_file=None)
    app = create_app(settings=settings)
    app.state.health_checks = _FakeChecks(rabbitmq_ok=True)
    app.state.openstack_available = False
    response = TestClient(app).get("/health/ready")
    assert response.status_code == 200
    assert "openstack" not in response.json()["checks"]
