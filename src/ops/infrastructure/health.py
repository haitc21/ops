"""Dependency health checks for readiness probes."""

from __future__ import annotations

from typing import Any

import aio_pika

from ops.config import Settings


class HealthChecks:
    """Probe RabbitMQ connectivity only."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def check_rabbitmq(self) -> dict[str, Any]:
        try:
            connection = await aio_pika.connect_robust(
                self._settings.require_rabbitmq_url,
                timeout=5,
            )
            await connection.close()
            return {"status": "up"}
        except Exception as exc:  # noqa: BLE001 - readiness must never raise
            return {"status": "down", "message": type(exc).__name__}
