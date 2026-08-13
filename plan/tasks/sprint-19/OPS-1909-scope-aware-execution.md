# OPS-1909 — Declared-scope OpenStack execution enforcement

**Status:** Selected — expedited  
**Points:** 8  
**Paired task:** CPS-1909  
**Depends on:** OPS-1201, OPS-1202  
**Blocks:** OPS-1906, OPS-1907, OPS-1908

## Testable outcome

OPS uses exactly the CPS-selected connection and declared execution purpose for
each command, rejects inconsistent scope before OpenStack access, and reports a
provider `403` as authorization failure rather than unsupported capability or a
successful partial inventory.

## Required behavior

- Validate `ADMIN_SYSTEM`, `ADMIN_PROJECT`, or `TENANT_PROJECT` execution
  purpose against connection resolution, required operation scope, provider,
  region, and safe CPS authorization-decision context.
- Construct system-scoped sessions only for `ADMIN_SYSTEM`; construct project
  sessions for `ADMIN_PROJECT` and `TENANT_PROJECT` with the exact resolved
  project/domain. OPS never resolves or retries another connection.
- Preserve service discovery `unsupported` separately from authenticated
  `401/403`. Required-collection authorization failure terminates the inventory
  operation without deletion finalization.
- Resource handlers revalidate provider ownership/project preconditions before
  mutation. Member commands cannot invoke flavor/image administration handlers.
- Keep credentials in bounded memory and exclude tokens/raw provider bodies
  from result, error, trace, fixture, and log data.

## RED tests and expected failures

- System token receiving Glance `403` produces normalized non-retryable
  authorization failure, not `SKIPPED_UNSUPPORTED`/`SUCCEEDED`.
- CPS-selected `ADMIN_PROJECT` produces `project_name` auth without
  `system_scope`; `ADMIN_SYSTEM` produces `system_scope=all` without tenant
  fallback.
- Purpose/scope/project mismatch fails before `openstack.Connection` or any SDK
  proxy method is called.
- Tenant command targeting another project, admin catalog command with tenant
  purpose, duplicate delivery, timeout, 401/403/404/429/5xx, and result publish
  failure have deterministic outcomes.

## Contract, live verification, and cleanup

Pin the canonical CPS execution-context schema/checksum before handler changes;
unknown major versions fail safely and approved additive fields remain
compatible. Live-test through CPS with admin-system, admin-project, and two
tenant projects; compare token scope/resource project with OpenStack CLI and
prove no cross-connection fallback. The routing test creates no provider
resource; record zero mutation and any task-created connection cleanup in
`cps/docs/runbooks/sprint-19-role-connection-routing.md`.

## Proposed commit

`fix(scope): enforce declared OpenStack execution context`
