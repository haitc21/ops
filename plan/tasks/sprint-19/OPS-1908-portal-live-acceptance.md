# OPS-1908 — API-driven compatibility and live acceptance

**Status:** Planned  
**Points:** 8  
**Paired task:** CPS-1908  
**Depends on:** OPS-1907, OPS-1909, CPS-1910

## Testable outcome

Every CPS flavor/image action reaches the intended OpenStack resource once,
recovers deterministically, and leaves no disposable provider state.

## Acceptance

- Execute the CPS API → RabbitMQ → OPS → OpenStack matrix for admin CRUD/
  access/lifecycle and member create/rebuild/resize/volume-from-image selection.
- Prove replay, OPS restart before/after mutation, publish failure, unsupported
  capability, provider conflict, authorization denial, and client retry.
- Compare provider ID plus material flavor/image fields after each operation;
  HTTP success or UI toast alone is insufficient.
- Run focused/full OPS gates, contract checksum, diff/secret/security scans and
  independently reviewed final diff.

## Cleanup and proposed commit

Delete only task-created images/flavors/instances/volumes and verify absence by
OpenStack CLI plus CPS reconciliation. Add the OPS timeline and cleanup ledger
to `cps/docs/runbooks/sprint-19-portal-parity.md`.

Proposed commit: `test(sprint-19): prove catalog provider parity`
