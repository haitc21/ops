# Sprint 1B — RabbitMQ runtime and handler dispatch

**Dates:** starts only after CPS CP2 and transport-contract review
**Capacity:** 13 stretch points in the separately gated 1B-Messaging increment
**Sprint Goal:** OPS declares durable RabbitMQ topology with publisher confirms, manual ack, retry/DLX, and graceful shutdown; validates and dispatches versioned envelopes without OpenStack or credential resolution.

## Stretch stories — 1B-Messaging

| Story | Points | CPS dependency | Increment | Status |
|---|---:|---|---|---|
| OPS-102 — RabbitMQ topology and robust runtime | 8 | CPS-101 (contracts) | 1B-Messaging | Ready |
| OPS-104 — Handler dispatch and envelope validation | 5 | OPS-101..103, OPS-102 | 1B-Messaging | Ready |

OPS-102 remains **whole**: topology, consumer, publisher confirms, manual acknowledgement, reconnect, retry queues, DLX, and graceful shutdown are delivered together.

## Deferred (not Sprint 1B)

- OPS-201 credential resolver and OpenStack connection factory.
- Real provider command handlers beyond validation/dispatch stubs.

## Delivery order

1. Wait for CPS-103..105 persistence foundation (no OPS dependency).
2. Apply contract delta pin if Sprint 1B adds retry/envelope metadata.
3. OPS-102 — complete messaging runtime.
4. OPS-104 — envelope validation and handler dispatch.
5. Integration with CPS-106 outbox/inbox (Compose RabbitMQ; no sibling checkout paths).

## Definition of Done

See canonical plan §Definition of Done.

## Implementation plan

- Canonical from workspace root: `cps/docs/superpowers/plans/2026-07-20-sprint-1b-persistence-operations-messaging.md`
- OPS working copy: `docs/superpowers/plans/2026-07-20-sprint-1b-persistence-operations-messaging.md`
