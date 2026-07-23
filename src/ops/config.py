"""Typed application settings for OPS."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

EnvironmentName = Literal["development", "test", "production"]


class Settings(BaseSettings):
    """Environment-backed OPS configuration."""

    model_config = SettingsConfigDict(
        env_prefix="OPS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: EnvironmentName = "development"
    service_name: str = "ops"
    log_level: str = "INFO"
    rabbitmq_url: str | None = None
    cps_base_url: str | None = None
    cps_timeout_seconds: int = 30
    openstack_timeout_seconds: int = 30
    openstack_verify_tls: bool = True
    worker_prefetch_count: int = 10
    worker_shutdown_grace_seconds: float = 30.0
    api_host: str = "0.0.0.0"
    api_port: int = 8001

    @model_validator(mode="after")
    def validate_required_settings(self) -> Settings:
        if self.environment in {"development", "test"}:
            if not self.rabbitmq_url:
                self.rabbitmq_url = "amqp://cmp:cmp_dev_password@127.0.0.1:5672/cmp"
            if not self.cps_base_url:
                self.cps_base_url = "http://127.0.0.1:8002"
            return self

        missing: list[str] = []
        if not self.rabbitmq_url:
            missing.append("OPS_RABBITMQ_URL")
        if not self.cps_base_url:
            missing.append("OPS_CPS_BASE_URL")
        if missing:
            raise ValueError(f"missing required production settings: {', '.join(missing)}")
        return self

    @property
    def require_rabbitmq_url(self) -> str:
        if not self.rabbitmq_url:
            raise RuntimeError("rabbitmq_url is not configured")
        return self.rabbitmq_url

    @property
    def require_cps_base_url(self) -> str:
        if not self.cps_base_url:
            raise RuntimeError("cps_base_url is not configured")
        return self.cps_base_url


@lru_cache
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
