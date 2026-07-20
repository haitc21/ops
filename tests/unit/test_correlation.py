"""OPS-002: correlation and operation ID propagation."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ops.main import create_app


def test_correlation_id_generated_when_missing() -> None:
    app = create_app()

    @app.get("/_test/ping")
    def ping() -> dict[str, str]:
        return {"status": "ok"}

    response = TestClient(app).get("/_test/ping")
    assert response.status_code == 200
    assert response.headers["x-correlation-id"]


def test_correlation_and_operation_ids_accepted() -> None:
    app = create_app()

    @app.get("/_test/ping")
    def ping() -> dict[str, str]:
        return {"status": "ok"}

    response = TestClient(app).get(
        "/_test/ping",
        headers={
            "X-Correlation-ID": "corr-1",
            "X-Operation-ID": "op-1",
            "X-Message-ID": "msg-1",
        },
    )
    assert response.headers["x-correlation-id"] == "corr-1"
    assert response.headers["x-operation-id"] == "op-1"
    assert response.headers["x-message-id"] == "msg-1"
