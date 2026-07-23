"""OPS-002: typed settings validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError


def test_production_settings_require_rabbitmq_and_cps_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPS_ENVIRONMENT", "production")
    monkeypatch.delenv("OPS_RABBITMQ_URL", raising=False)
    monkeypatch.delenv("OPS_CPS_BASE_URL", raising=False)

    from ops.config import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_development_settings_include_openstack_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPS_ENVIRONMENT", "development")
    monkeypatch.delenv("OPS_RABBITMQ_URL", raising=False)
    monkeypatch.delenv("OPS_CPS_BASE_URL", raising=False)

    from ops.config import Settings

    settings = Settings(_env_file=None)
    assert settings.rabbitmq_url.startswith("amqp")
    assert settings.cps_base_url == "http://127.0.0.1:8002"
    assert settings.openstack_timeout_seconds > 0
    assert settings.openstack_verify_tls is True
