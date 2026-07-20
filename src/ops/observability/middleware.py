"""ASGI middleware for request/message correlation."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

CORRELATION_HEADER = "X-Correlation-ID"
OPERATION_HEADER = "X-Operation-ID"
MESSAGE_HEADER = "X-Message-ID"


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        correlation = request.headers.get(CORRELATION_HEADER)
        correlation_id = (
            correlation.strip() if correlation and correlation.strip() else str(uuid.uuid4())
        )
        operation_id = request.headers.get(OPERATION_HEADER)
        message_id = request.headers.get(MESSAGE_HEADER)
        request.state.correlation_id = correlation_id
        request.state.operation_id = operation_id
        request.state.message_id = message_id
        response = await call_next(request)
        response.headers[CORRELATION_HEADER] = correlation_id
        if operation_id:
            response.headers[OPERATION_HEADER] = operation_id
        if message_id:
            response.headers[MESSAGE_HEADER] = message_id
        return response
