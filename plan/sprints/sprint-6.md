# Sprint 6 — OPS design alignment and fast demo readiness

**Dates:** 2026-07-23 onward

**Sprint Goal:** Make OPS capability reporting, lifecycle completion, and
resource mapping match the approved OpenStackSDK-based design without blocking
the CPS/OPS demo on production-only hardening.

**Canonical plan:**
`../../../cps/docs/superpowers/plans/2026-07-23-sprint-6-design-alignment-demo.md`

## Selected stories

| Story | Points | Owner | CPS dependency | Status |
|---|---:|---|---|---|
| OPS-601 Capability/version discovery | 8 | OPS | Pinned capability contract | Ready |
| OPS-602 Delete waiter/convergence | 5 | OPS | Operation event persistence | Ready |
| CPS-603 Production key-ring fail-fast | 3 | CPS | None | Ready |
| OPS-603 Recursive mapper sanitization | 5 | OPS | Inventory contracts | Ready |
| OPS-604 Immutable OPS runtime image | 2 | OPS | None | Ready |

## Delivery tasks

- [ ] Inspect and use supported OpenStackSDK version/proxy APIs.
- [ ] Add failing capability, waiter, replay, and mapper-boundary tests.
- [ ] Implement P0 behavior without direct service HTTP clients.
- [ ] Verify retry, timeout, replay, and redaction behavior.
- [ ] Run the local Compose demo with CPS internal resolver DNS.
- [ ] Harden the OPS image after the first three technical slices and demo checkpoint.
- [ ] Run the OPS Definition of Done quality gates.

## Deferred dependency

CPS production key-ring fail-fast is tracked as CPS-603. It is part of this
sprint's original priority order and must not be bypassed by embedding a
default key.
