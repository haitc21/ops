# Sprint 1 — Pinned contracts and OpenStack error semantics

**Dates:** 2026-07-31 to 2026-08-14
**Capacity:** 13 committed points in OPS; no partial messaging story
**Sprint Goal:** OPS pins CPS fixtures and JSON Schemas with a standalone drift guard, then normalizes OpenStackSDK errors and produces deterministic retry decisions without consuming messages or calling OpenStack.

## Committed stories — Must

| Story | Points | Owner | CPS dependency | Status |
|---|---:|---|---|---|
| OPS-101 — Pin and validate CPS contracts | 5 | Unassigned | CPS-101 | Done |
| OPS-103 — Error normalization and retry policy | 8 | Unassigned | CPS-102 | Done |

**Total:** 13 points.

## Deferred to Sprint 1B

- OPS-102 — RabbitMQ topology and robust runtime.
- OPS-104 — Handler dispatch and envelope validation.

OPS-102 remains whole: topology, consumer, publisher confirms, manual acknowledgement, reconnect, retry queues, DLX, and graceful shutdown will be planned and delivered together. It is not partially counted in Sprint 1.

## Delivery order

1. Mirror manifest-algorithm change from CPS.
2. Wait for final CPS-101/102 manifest.
3. OPS-101 copies fixtures, JSON Schemas, live manifest, and pinned canonical manifest.
4. OPS-103 maps SDK/network/timeout failures into the pinned common error model.
5. Verify deterministic exponential backoff, jitter, Retry-After, and exhaustion classification.

## Definition of Done

- Local contract tree matches its checksum manifest.
- Live OPS manifest equals `cps_checksums.pinned.json` byte-for-byte.
- No test or CI path depends on `C:\work` or a sibling checkout.
- Auth, forbidden, missing, conflict, rate-limit, 5xx, timeout, and network failures map to stable errors.
- Provider request ID is retained; raw response body is excluded.
- Retry classification is deterministic in tests and does not claim RabbitMQ delivery behavior.
- OPS retains no database dependency and makes no OpenStack API request.
- Ruff, mypy, pytest, contracts, secret scan, Docker build, and `git diff --check` pass.

## Risks

| Risk | Mitigation |
|---|---|
| Pin updated independently of CPS | Require live manifest and pinned snapshot in the same reviewed copy commit. |
| SDK exception hierarchy differs | Parametrized tests use OpenStackSDK 4.17.0 public exception classes. |
| Retry classification mistaken for delivery retry | `RetryDecision` contains timing/exhaustion only; ack/requeue belongs to Sprint 1B. |

## Review evidence

- Verification date: **2026-07-20** (Sprint 1A Task 5, branch `main`, HEAD `3bcd81f`).
- Story commits:
  - OPS-101 — `a19a959` (`feat: initialize OPS service`; pinned contracts, manifest parity, standalone pin guard)
  - OPS-103 — `3bcd81f` (`feat(OPS-103): normalize OpenStack errors and classify retries`)
- Final DoD gates (fresh run, exit 0 unless noted):
  - `py -3.12 -m uv lock --check` — ok
  - `py -3.12 -m uv sync --frozen --all-extras` — ok
  - `py -3.12 -m uv run ruff format --check src tests` — ok
  - `py -3.12 -m uv run ruff check src tests` — ok
  - `py -3.12 -m uv run mypy` — ok (33 source files)
  - `py -3.12 -m uv run pytest tests/unit/openstack -q -W error::openstack.warnings.RemovedInSDK50Warning` — **42 passed**
  - `py -3.12 -m uv run pytest -q` — **131 passed, 5 skipped**
  - `py -3.12 -m uv run python -m ops.contracts.validate_contracts` — ok (**8 manifest-managed contract files**: 6 fixtures + 2 JSON Schemas)
  - standalone pin assertion (`assert_matches_cps_canonical`) — ok
  - read-only secret verification — ok (`detect-secrets-hook --baseline .secrets.baseline` per tracked file from `git ls-files -z`, repo exclude regex, NUL-safe argv; **77 files scanned**; baseline SHA unchanged `2edc11c5338fe3265a7dec347942bc3ade7739ee29cc641eaaf7e17503f0e7cd`)
  - `git diff --check` — ok
  - `docker build -t ops:sprint1a .` — ok
  - host `.husky/pre-commit` — **exit 0**
- Pinned manifest SHA-256 (`src/ops/contracts/cps_checksums.pinned.json`): `79f4d97a07e53357210ede4f905c65d905776aa12952e06280b5ad7d6532bc43`.
- Pin equality: live `checksums.json` bytes == `cps_checksums.pinned.json` == CPS canonical manifest (byte-equal True; SHA-256 `79f4d97a07e53357210ede4f905c65d905776aa12952e06280b5ad7d6532bc43`).
- Contract count fix (Task 5 finding): `ValidationResult.file_count` now reports `len(manifest files)` (8), not semantic fixture count (6); the CLI and manifest writer use the same unambiguous field; regression test `test_validate_success_reports_manifest_managed_file_count`.
- Warnings: 1 pre-existing `StarletteDeprecationWarning` (httpx vs httpx2 in FastAPI testclient); **0** `RemovedInSDK50Warning` under focused OpenStack suite with `-W error`.
- Known limitations:
  - No CPS → OPS runtime integration to supply provider connection/credential (CPS owns connection and credential in PostgreSQL).
  - No credential resolution, RabbitMQ topology/consumer, ack/requeue/DLQ, or handler dispatch (deferred OPS-102/104).
  - No OpenStack API calls or real-cloud integration tests in Sprint 1A.
  - `RetryDecision` is timing/exhaustion only; messaging delivery policy belongs to Sprint 1B.
  - Readiness checks RabbitMQ only; OpenStack connectivity excluded from `/health/ready`.

## Sprint Review

- OPS-101 pins CPS fixtures, JSON Schemas, and checksum manifest with standalone validation and Husky pin guard (no sibling checkout path).
- OPS-103 normalizes OpenStackSDK, requests, and keystoneauth1 exceptions into pinned `CommonError`; HTTP status precedence, HTTP 408 timeout, allowlisted request-ID headers; deterministic `RetryDecision` without ack/requeue/DLQ.
- Cross-repo contract byte parity confirmed against CPS canonical manifest.

## Sprint Retrospective

- Keep: two-phase HTTP status then class fallback; synthetic-secret leak tests; SDK50 warning as error in focused suite; standalone pin assertion in Husky.
- Improve: document squashed initialize commit for OPS-101 when git history is compact; use read-only `detect-secrets-hook` full-tracked verification (not `scan --baseline`) for evidence gates.
- Sprint 1B handoff: new reviewed plan required for OPS-102 (whole messaging story) and OPS-104; CPS side needs CPS-103..106 plan before persistence/outbox work.

## Implementation plan

- Canonical from workspace root: `cps/docs/superpowers/plans/2026-07-17-sprint-1-contracts-operations-messaging.md`
- OPS working copy: `docs/superpowers/plans/2026-07-17-sprint-1-contracts-operations-messaging.md`
