# OPS-1909 declared-scope execution implementation plan

## Story, contract, and blast radius

Story: OPS-1909, paired CPS-1909. OPS uses exactly the CPS-selected connection,
validates its declared purpose/scope before SDK access, never falls back to
another connection, and distinguishes provider authorization from unsupported
capability.

- CPS canonical schema/version/checksum must land first; OPS pins it without
  drift. Unknown major fails before credential resolution/OpenStack access.
- Expected surfaces: `src/ops/contracts/validation.py` and pinned schemas/
  fixtures/checksum, `application/credential_resolver.py`, handler validation,
  `openstack/factory.py`, `openstack/scope.py`, `inventory_collect.py`, resource
  handlers, error normalization, and focused contract/unit/messaging tests.
  Rerun CodeGraph before implementation to confirm exact callers.
- No database, CPS authorization decision, alternate-connection lookup, raw
  provider body, or credential persistence belongs in OPS.
- Threat scope: declared/resolved scope mismatch, cross-tenant execution,
  malicious command replay, credential leakage, authorization downgrade to
  unsupported, and unsafe partial-sync finalization.

## Bite-sized RED–GREEN–REFACTOR checklist

- [ ] Invoke required skills/security workflow; create isolated worktree and
      record clean status/base hash.
- [ ] Pin CPS contract; RED contract tests for purpose/scope combinations,
      additive fields, unknown major, and missing decision context.
- [ ] GREEN minimal typed validation before credential resolution.
- [ ] RED factory tests proving exact `ADMIN_SYSTEM` system scope and exact
      `ADMIN_PROJECT`/`TENANT_PROJECT` project scope with no alternate lookup;
      observe failures, then implement minimal GREEN.
- [ ] RED inventory tests: Glance 403 is terminal authorization failure and
      prevents successful completion/deletion finalization; discovery absence
      alone is `SKIPPED_UNSUPPORTED`. Observe failures, then minimal GREEN.
- [ ] RED resource-handler tests for admin/member operation-purpose mismatch,
      project ownership mismatch, duplicate/redelivery, timeout, 401/403/404/
      429/5xx, publish failure, and restart. Implement minimal GREEN.
- [ ] REFACTOR shared execution-context validation without a generic provider
      client; rerun focused tests after every change.
- [ ] Run RabbitMQ affected integration and CPS/OPS checksum parity.
- [ ] Independent two-pass review; remediate and re-review all valid findings.
- [ ] Security diff/secret scans with no unresolved Critical/High.

## Verification, live test, cleanup, and commit

- Run focused contract/factory/scope/inventory/handler tests; full OPS frozen
  install, format, lint, typing, test, contract, checksum, diff, and secret gates.
- Through CPS, live-test admin-system, admin-project, and two tenant connections;
  compare token/project/resource IDs with OpenStack CLI. Exercise real Glance
  403, duplicate, restart, publish failure, and mismatched context; prove no
  cross-connection request occurs.
- Create no provider resource. Remove only task-created CPS test connections and
  record zero provider mutation in
  `cps/docs/runbooks/sprint-19-role-connection-routing.md`.
- Proposed commit: `fix(scope): enforce declared OpenStack execution context`.

Stop after verified diff; do not stage/commit/push without explicit current-turn
authorization.
