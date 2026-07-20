"""OPS-001: application factory acceptance tests."""

from __future__ import annotations

import importlib.metadata
import sys

import pytest
from fastapi import FastAPI


def test_runtime_is_cpython_3_12() -> None:
    assert sys.version_info[:2] == (3, 12)


def test_create_app_returns_fastapi_application() -> None:
    from ops.main import create_app

    app = create_app()
    assert isinstance(app, FastAPI)
    assert app.title == "OPS"


def test_dependencies_exclude_persistence_stacks() -> None:
    names = {dist.metadata["Name"].lower() for dist in importlib.metadata.distributions()}
    forbidden = {
        "sqlalchemy",
        "alembic",
        "psycopg",
        "psycopg-binary",
        "pymongo",
        "redis",
        "valkey",
    }
    present = sorted(names & forbidden)
    assert present == []


def test_openstacksdk_is_pinned() -> None:
    version = importlib.metadata.version("openstacksdk")
    assert version == "4.17.0"


def test_cli_main_exposes_help(capsys: pytest.CaptureFixture[str]) -> None:
    from ops.cli import main

    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "ops" in captured.out.lower() or "usage" in captured.out.lower()
