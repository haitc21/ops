"""OPS-003: readiness against local RabbitMQ."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from ops.config import Settings
from ops.main import create_app

pytestmark = pytest.mark.integration


@pytest.mark.skipif(os.getenv("OPS_RUN_INTEGRATION", "0") != "1", reason="integration disabled")
def test_readiness_succeeds_against_local_rabbitmq() -> None:
    settings = Settings(
        environment="test",
        rabbitmq_url=os.getenv(
            "OPS_RABBITMQ_URL",
            "amqp://cmp:cmp_dev_password@127.0.0.1:5672/cmp",
        ),
        _env_file=None,
    )
    client = TestClient(create_app(settings=settings))
    assert client.get("/health/live").status_code == 200
    ready = client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["checks"]["rabbitmq"]["status"] == "up"
    assert "openstack" not in ready.json()["checks"]
