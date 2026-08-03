# OPS-1907 — Flavor and image provider lifecycle parity closure

**Status:** Planned  
**Points:** 13  
**Paired task:** CPS-1907  
**Depends on:** OPS-1906

## Testable outcome

OpenStackSDK handlers converge all safe flavor/image administration actions
required by CPS and return deterministic terminal results under replay,
partial failure, timeout, unsupported APIs, and restart.

## Required behavior

- Flavor: create/delete, project access convergence, extra-spec add/change/
  remove; core sizing remains immutable and is never updated by delete/create.
- Image: metadata/property deltas, visibility/protection, members, deactivate/
  reactivate/delete, snapshot convergence, and allowlisted HTTPS import when
  the discovered SDK capability and source policy both allow it.
- Reuse existing handler/discovery/retry/publish infrastructure. Validate before
  credential resolution or provider mutation, use bounded waiters, publish
  terminal result before ack, and sanitize request IDs/errors.

## RED and verification

Write failing SDK/messaging tests for every new gap plus duplicate delivery,
already-converged state, partial member/spec update, protected/status conflict,
malicious URL/metadata, 401/403/404/409/429/5xx, publish failure, timeout, and
worker restart. Run focused and full OPS gates, diff/secret/security scans.

Live-test through CPS, compare each material state with OpenStack CLI, then
delete task-created resources and prove absence. Record evidence in
`cps/docs/runbooks/sprint-19-portal-parity.md`.

Proposed commit: `feat(resources): close flavor and image provider parity gaps`
