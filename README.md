# OPS

OpenStack Provider Service — stateless OpenStack adapter for CPS.

Canonical design lives in the sibling CPS repository:
`docs/superpowers/specs/2026-07-16-openstack-cloud-provider-management-design.md`.

## Requirements

- CPython 3.12 (do not use Python 3.14)
- [uv](https://github.com/astral-sh/uv) for locked installs
- Local RabbitMQ from `../cps/deploy/docker`

## Setup

```bash
uv sync --all-extras --frozen
```

## Run

```bash
export OPS_CPS_BASE_URL=http://127.0.0.1:8002
uv run ops serve --host 127.0.0.1 --port 8001
uv run ops worker --once
```

`OPS_CPS_BASE_URL` must point to the CPS internal listener (`:8002` in the
development environment), not the public CPS listener on `:8000`. The
development settings use this internal URL by default; set the variable
explicitly in deployment environments.

### Docker

Build and run the OPS API:

```bash
docker build -t cmp-ops .
docker run --rm --env-file .env -p 8001:8001 \
  -e OPS_CPS_BASE_URL=http://127.0.0.1:8002 cmp-ops
```

## Quality gates

```bash
uv sync --frozen --all-extras
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy
uv run pytest -q
uv run python -m ops.contracts.validate_contracts
```

Staged read-only secret verification runs via `bash .husky/pre-commit` (install
with `npm install`). Do not use `detect-secrets scan --baseline` as a
verification command.

Integration tests against RabbitMQ are opt-in:

```bash
OPS_RUN_INTEGRATION=1 uv run pytest -q
```

Windows (Python launcher): use `py -3.12 -m uv` instead of `uv`; set integration with `$env:OPS_RUN_INTEGRATION="1"`.

## Contract maintenance

After changing manifest-managed contract files:

```bash
uv run python -m ops.contracts.write_manifest
```

Commit the updated checksum manifest explicitly. This is not a verification gate.
