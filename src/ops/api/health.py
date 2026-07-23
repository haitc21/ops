"""Liveness and readiness endpoints."""

from __future__ import annotations

from typing import Any, Protocol

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import PlainTextResponse

router = APIRouter(tags=["health"])


class SupportsHealthChecks(Protocol):
    async def check_rabbitmq(self) -> dict[str, Any]: ...


@router.get("/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness(request: Request, response: Response) -> dict[str, Any]:
    checks: SupportsHealthChecks = request.app.state.health_checks
    rabbitmq = await checks.check_rabbitmq()
    payload = {
        "status": "ok",
        "checks": {
            "rabbitmq": rabbitmq,
        },
    }
    if rabbitmq.get("status") != "up":
        payload["status"] = "error"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return payload


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics_endpoint(request: Request) -> str:
    return str(request.app.state.metrics.render_prometheus())
