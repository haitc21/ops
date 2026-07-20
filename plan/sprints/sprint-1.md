# Sprint 1 — Pinned contracts and OpenStack error semantics

**Dates:** 2026-07-31 to 2026-08-14
**Capacity:** 13 committed points in OPS; no partial messaging story
**Sprint Goal:** OPS pins CPS fixtures and JSON Schemas with a standalone drift guard, then normalizes OpenStackSDK errors and produces deterministic retry decisions without consuming messages or calling OpenStack.

## Committed stories — Must

| Story | Points | Owner | CPS dependency | Status |
|---|---:|---|---|---|
| OPS-101 — Pin and validate CPS contracts | 5 | Unassigned | CPS-101 | Ready |
| OPS-103 — Error normalization and retry policy | 8 | Unassigned | CPS-102 | Ready |

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

- Test counts: fill only after commands run.
- Pinned manifest SHA-256: fill after OPS-101 copies the final CPS contracts.
- Known limitations: no RabbitMQ topology/consumer, acknowledgement policy, credential resolution, or provider call.

## Implementation plan

- Canonical from workspace root: `cps/docs/superpowers/plans/2026-07-17-sprint-1-contracts-operations-messaging.md`
- OPS working copy: `docs/superpowers/plans/2026-07-17-sprint-1-contracts-operations-messaging.md`
