# OPS-1304 — Reconcile stale VM-create operations

**Status:** Completed — bounded provider lookup and terminal convergence verified in the joint acceptance flow.

## Goal

Answer a CPS reconciliation command with provider truth without repeating the
original create mutation.

## Design

- Validate the reconcile command and resolve the provider connection normally.
- Find the server by provider resource ID, falling back to exact
  `cmp_operation_id` metadata only when necessary.
- Perform read-only Nova state inspection with bounded SDK calls.
- Optionally collect bounded port/volume snapshots after terminal Nova state.
- Return deterministic completed, failed, absent, or retryable-unavailable
  outcomes.
- Never call `create_server` from the reconciliation handler.

## Acceptance

- `ACTIVE` and `SHUTOFF` return a successful normalized instance result.
- `ERROR` returns a normalized failed outcome.
- Provider-confirmed absence returns an explicit absent outcome for CPS timeout
  policy; transient provider failure is not reported as absence.
- Replay and concurrent reconciliation are read-only and idempotent.
- The known ACTIVE server
  `a64e3ca9-d396-4357-8396-fd989ad288ce` can close its stale CPS operation.
