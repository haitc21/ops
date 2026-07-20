# OPS

OpenStack Provider Service — stateless OpenStack adapter for CPS.

Canonical design lives in the sibling CPS repository:
`docs/superpowers/specs/2026-07-16-openstack-cloud-provider-management-design.md`.

## Requirements

- CPython 3.12 (do not use Python 3.14)
- [uv](https://github.com/astral-sh/uv) for locked installs
- Local RabbitMQ from `../cps/deploy/docker`

## Setup

```powershell
py -3.12 -m uv sync --all-extras --frozen
```

## Run

```powershell
py -3.12 -m uv run ops serve --host 127.0.0.1 --port 8001
py -3.12 -m uv run ops worker --once
```

## Quality gates

```powershell
py -3.12 -m uv sync --frozen --all-extras
py -3.12 -m uv run ruff format --check src tests
py -3.12 -m uv run ruff check src tests
py -3.12 -m uv run mypy
py -3.12 -m uv run pytest -q
py -3.12 -m uv run python -m ops.contracts.validate_contracts
py -3.12 -m uv run python -m ops.contracts.write_manifest
py -3.12 -m uv run python -m detect_secrets scan --baseline .secrets.baseline --exclude-files "(?i)(.*\.venv/.*|.*uv\.lock$|.*\.git/.*|(.*/)?(checksums|cps_checksums\.pinned)\.json$)"
```

Integration tests against RabbitMQ are opt-in:

```powershell
$env:OPS_RUN_INTEGRATION="1"
py -3.12 -m uv run pytest -q
```
