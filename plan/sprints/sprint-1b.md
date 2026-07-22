# Sprint 1B — RabbitMQ runtime and handler dispatch

**Status:** Done
**Dates:** closed **2026-07-22** after CP4/CP5 evidence review and independent approval
**Capacity:** 13 stretch points in the separately gated 1B-Messaging increment — all delivered
**Sprint Goal:** OPS declares durable RabbitMQ topology with publisher confirms, manual ack, retry/DLX, and graceful shutdown; validates and dispatches versioned envelopes without OpenStack or credential resolution.

## Stretch stories — 1B-Messaging

| Story | Points | CPS dependency | Increment | Status |
|---|---:|---|---|---|
| OPS-102 — RabbitMQ topology and robust runtime | 8 | CPS-101 (contracts) | 1B-Messaging | Done |
| OPS-104 — Handler dispatch and envelope validation | 5 | OPS-101..103, OPS-102 | 1B-Messaging | Done |

**Stretch total:** 13 points — all Done.

OPS-102 remains **whole**: topology, consumer, publisher confirms, manual acknowledgement, reconnect, retry queues, DLX, and graceful shutdown are delivered together.

## Deferred (not Sprint 1B — Sprint 2+)

- OPS-201 credential resolver and OpenStack connection factory.
- Real provider command handlers beyond validation/dispatch stubs.
- Provider/credential/connection CRUD APIs, inventory/VM lifecycle, Keycloak/TMS/LMS/CMP integration.

## Delivery order and review checkpoints

1. Wait for CPS-103..105 persistence foundation (no OPS dependency) — **Done** (CPS side).
2. Apply contract delta pin if Sprint 1B adds retry/envelope metadata — **Done** (CP3).
3. OPS-102 — complete messaging runtime — **Done** (CP4).
4. OPS-104 — envelope validation and handler dispatch — **Done** (CP5).
5. Integration with CPS-106 outbox/inbox (Compose RabbitMQ; no sibling checkout paths) — **Done** (CP6, cross-repo).

## Definition of Done

All OPS items in the canonical plan §Definition of Done are satisfied with fresh evidence dated **2026-07-22**. See `docs/superpowers/plans/2026-07-20-sprint-1b-persistence-operations-messaging.md` §Sprint 1B closure evidence.

## Review evidence

- Verification date: **2026-07-22** (Task 12 closure gate; OPS HEAD `7318f53ee29f5e54a25b4dd0fd35034591cc0854`).
- Story commits:
  - OPS-102 — `8121235` (topology), `2ecaeb3` (consumer ack/retry/DLQ, publisher confirms, shutdown)
  - OPS-104 — `180031f` → `c6170de` → `7318f53ee29f5e54a25b4dd0fd35034591cc0854`
  - Transport contract pin — `d326164`
- Final DoD gates (fresh run, exit 0 unless noted):
  - `uv lock --check` / `uv sync --frozen --all-extras` — pass
  - `uv run pytest -q` (integration OFF) — **311 passed, 24 skipped** (1 known `StarletteDeprecationWarning`)
  - messaging integration (`OPS_RUN_INTEGRATION=1`) — **21 passed**
  - full integration ON — **333 passed, 2 skipped** (same warning)
  - OpenStack DeprecationWarning suite (`-W error::DeprecationWarning`) — **42 passed**
  - `uv run ruff format --check src tests` — **86** files / pass
  - `uv run ruff check src tests` — pass
  - `uv run mypy` — **47** files / pass
  - `uv run python -m ops.contracts.validate_contracts` + standalone pin — **10** artifacts / pass
  - cross-repo contract byte parity SHA-256: `2C19CB44550063383F4EBCD35E292B5377FEEDFC185B30F215117E6EA150A07D` (10 artifacts byte-equal)
  - `git diff --check` — pass
  - `docker build -t ops:sprint1b .` — exit 0
  - host `.husky/pre-commit` — exit 0 (including staged fix state)
  - read-only secret scan baseline unchanged `48EBCA6C0199E4331362AF974970DD49528CEAEB16C483208F0A226CF4058E8F`
- Independent review: **APPROVED** — no P0–P3.
- Architecture boundaries passed: no DB runtime in OPS; no sibling imports; no legacy identifiers; no GitHub Actions.

## Sprint Review

- OPS-102 delivered RabbitMQ topology (exchanges, queues, DLX, retry TTL tiers), consumer ack/retry/DLQ matrix, publisher confirms, reconnect with idempotent redeclare, and graceful shutdown.
- OPS-104 delivered envelope validation, typed handler dispatch by `message_type`, and stub connection-validate handler (no OpenStack, no credential resolution).
- Transport contract delta (`DeliveryMetadata`) pinned byte-for-byte from CPS canonical manifest.
- Cross-repo integration with CPS-106 outbox/inbox verified via Compose RabbitMQ (CP6).

## Sprint Retrospective

- **Keep:** exact ACK/confirm ordering tests; contract byte-parity gates; disposable integration guards; reject-only DLX path (no retry+DLX duplicate); channel-close recovery on confirm failure.
- **Improve:** record verification HEAD explicitly at closure (no self-referential docs-only commit SHA).
- **Sprint 2 handoff:** credential resolver (OPS-201), real OpenStack handlers, and provider CRUD remain deferred per product backlog.

## Implementation plan

- Canonical from workspace root: `cps/docs/superpowers/plans/2026-07-20-sprint-1b-persistence-operations-messaging.md`
- OPS working copy: `docs/superpowers/plans/2026-07-20-sprint-1b-persistence-operations-messaging.md`
