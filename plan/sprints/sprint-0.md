# Sprint 0 — Reproducible stateless service foundation

**Dates:** 2026-07-17 to 2026-07-31
**Capacity:** focused foundation delivery
**Sprint Goal:** OPS installs from a pinned CPython 3.12 lockfile, starts health API and worker entrypoints with typed RabbitMQ/OpenStack settings and secret-safe logging, readiness reflects RabbitMQ only, and CI quality gates pass without SQLAlchemy/PostgreSQL/MongoDB/Valkey dependencies.

## Selected stories

| Story | Points | Owner | CPS dependency | Status |
|---|---:|---|---|---|
| OPS-001 | 5 | Agent | none | Done |
| OPS-002 | 5 | Agent | none | Done |
| OPS-003 | 3 | Agent | none | Done |
| OPS-004 | 5 | Agent | none | Done |

## Delivery tasks

- [x] Confirm pinned CPS schema and golden fixture (none required for Sprint 0 provider operations).
- [x] Add failing mapper/handler acceptance tests (foundation tests for config/health/redaction).
- [x] Implement through supported OpenStackSDK dependency pin only (no provider operations).
- [x] Add RabbitMQ readiness integration coverage.
- [x] Test redaction and readiness behavior (OpenStack outage must not fail readiness).
- [x] Update operational documentation for local start/health.
- [x] Run the Definition of Done quality gates.
- [x] Harden worker lifecycle, logging service_name, read-only contract validation, integration opt-out, and cancellation shutdown path.

## Story details

### OPS-001 — Bootstrap a reproducible Python service

- **Depends on:** none
- **Acceptance:** clean locked install; worker and health API start; quality commands pass; no DB/Valkey dependencies; OpenStackSDK 4.17.0 present but unused for provider ops.
- **Verification:** lock install, entrypoint smoke, dependency absence checks, Python 3.12 runtime.

### OPS-002 — Typed configuration and secret-safe observability

- **Depends on:** OPS-001
- **Acceptance:** RabbitMQ/CPS/OpenStack timeout/TLS settings validate; structured logs redact credentials, tokens, auth headers, CA secrets, and user data; correlation/operation/message IDs propagate.
- **Verification:** settings and redaction unit tests.

### OPS-003 — Health/readiness lifecycle

- **Depends on:** OPS-001
- **Acceptance:** liveness process-only; readiness reflects RabbitMQ; customer OpenStack outage does not make OPS unready; graceful shutdown stops intake and finishes/nacks safely (foundation stub for intake lifecycle).
- **Verification:** unit + RabbitMQ integration tests.

### OPS-004 — Local quality pipeline

- **Depends on:** OPS-001..003
- **Acceptance:** Husky pre-commit runs format, lint, typing, default tests, contract validation, and secret scan. RabbitMQ integration and image build remain explicit developer/GitLab pipeline gates.
- **Verification:** workflow commands executed locally with evidence.

## Risks and impediments

| Risk/impediment | Owner | Mitigation | Status |
|---|---|---|---|
| OpenStackSDK pin must not imply readiness dependency | Agent | Readiness checks RabbitMQ only; no OpenStack probe in `/health/ready` | Mitigated |
| No CPS contracts yet to pin | Agent | Empty-safe read-only contract validation until Sprint 1 | Mitigated |
| Windows Ctrl+C / task cancel skipped shutdown | Agent | `begin_shutdown()` in `finally` before closing RabbitMQ | Mitigated |

## Review evidence

- Demo scenario: start OPS health API, call `/health/live` and `/health/ready` with RabbitMQ up; `ops worker` stays up and shuts down cleanly.
- Final DoD (2026-07-17, branch `sprint-0`):
  - `uv sync --frozen --all-extras` — ok
  - `ruff format --check`, `ruff check`, `mypy` — ok
  - `pytest -q` (default) — **25 passed, 1 skipped** (integration opt-out)
  - `OPS_RUN_INTEGRATION=1 pytest tests/integration` — **1 passed**
  - `python -m ops.contracts.validate_contracts` — ok (0 fixtures; read-only)
  - `python -m detect_secrets scan --baseline .secrets.baseline ...` — ok
  - `docker build -t ops:sprint0 .` — ok
  - `git diff --check` — clean
  - Compose postgres/rabbitmq/valkey remained healthy; OPS readiness uses RabbitMQ only
- Hardening commits: `af36259` (worker/logging/contracts/integration defaults + cancellation path)
- CPS contract checksum: empty Sprint 0 pin (`fixtures: {}`)
- Discovered OpenStack capability/version notes: N/A (no provider operations)

## Retrospective actions

- Keep: RabbitMQ-only readiness; OpenStackSDK pin without provider ops; cancel-safe worker `finally`.
- Improve: keep Windows Application Control workarounds for detect-secrets; integration opt-in locally.
- One measurable action for next sprint: pin CPS golden fixtures after CPS-101.
