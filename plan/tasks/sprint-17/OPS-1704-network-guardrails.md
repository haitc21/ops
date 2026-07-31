# OPS-1704 — Provider-side network guardrails

**Status:** Done
**Active backlog:** No
**Paired task:** CPS-1704

## Outcome

OPS rechecks tenant ownership, parent relationships, external-network type,
security rules, and current Neutron quota immediately before mutation.

## Evidence

- Unit coverage includes foreign parent resources, quota exhaustion, malformed
  topology, public ingress denial, and public egress allowance.
- Live CPS/OPS operations created a disposable tenant network, subnet, and
  router; cleanup verification found no residual resources.
