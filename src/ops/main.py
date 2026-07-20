"""FastAPI application factory for OPS."""

from __future__ import annotations

from fastapi import FastAPI

from ops.api.health import router as health_router
from ops.config import Settings, get_settings
from ops.infrastructure.health import HealthChecks
from ops.messaging.lifecycle import WorkerLifecycle
from ops.observability.logging import configure_logging
from ops.observability.middleware import CorrelationIdMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the OPS ASGI application."""
    resolved = settings or get_settings()
    configure_logging(level=resolved.log_level, service_name=resolved.service_name)

    app = FastAPI(title="OPS", version="0.1.0")
    app.state.settings = resolved
    app.state.health_checks = HealthChecks(resolved)
    app.state.worker_lifecycle = WorkerLifecycle()
    app.state.openstack_available = True
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(health_router)
    return app
