# Sprint 19 — OpenStack image and flavor administration adapter

**Status:** Extension selected — original OPS-1901..1905 complete; OPS-1906..1908 planned
**Dates:** 2026-08-03 to 2026-08-14
**Capacity:** 34 delivered + 29-point Horizon parity extension
**Sprint Goal:** OPS safely executes and reconciles the image/flavor
administration commands defined by CPS, using supported OpenStackSDK APIs and
returning provider-neutral, replay-safe results.

## Selected stories

| Story | Points | Owner | CPS dependency | Status |
|---|---:|---|---|---|
| OPS-1901 Catalog mapper and capability enrichment | 5 | OPS | CPS-1901 | Done |
| OPS-1902 Flavor lifecycle handlers | 8 | OPS | CPS-1902 | Done |
| OPS-1903 Image metadata/access/lifecycle handlers | 13 | OPS | CPS-1903 | Done |
| OPS-1904 Instance image snapshot and compatibility | 5 | OPS | CPS-1904 | Done |
| OPS-1905 Real-cloud acceptance and cleanup | 3 | CPS/OPS | CPS-1905 | Done (waived restart/failure path) |
| OPS-1906 Horizon semantic parity and catalog enrichment | 8 | OPS | CPS-1906 | Deferred — implementation committed; TMS/scope-policy closure pending |
| OPS-1907 Flavor and image provider lifecycle parity closure | 13 | OPS | CPS-1907 | Planned |
| OPS-1908 API-driven compatibility and live acceptance | 8 | CPS/OPS | CPS-1908 | Planned |

## Task backlog

| Task | Deliverable | Depends on | Status |
|---|---|---|---|
| [OPS-1901](../tasks/sprint-19/OPS-1901-catalog-mappers.md) | Pinned contracts, detailed mappers, capabilities | OPS-1703 | Done |
| [OPS-1902](../tasks/sprint-19/OPS-1902-flavor-lifecycle.md) | Replay-safe Nova flavor handlers | OPS-1901 | Done |
| [OPS-1903](../tasks/sprint-19/OPS-1903-image-lifecycle.md) | Replay-safe Glance image handlers | OPS-1901 | Done |
| [OPS-1904](../tasks/sprint-19/OPS-1904-instance-image-integration.md) | Nova snapshot and source compatibility behavior | OPS-1901, OPS-1903 | Done |
| [OPS-1905](../tasks/sprint-19/OPS-1905-catalog-acceptance.md) | Live compatibility/recovery/cleanup evidence | OPS-1902..1904 | Done (waived restart/failure path) |
| [OPS-1906](../tasks/sprint-19/OPS-1906-horizon-semantic-parity.md) | Nova/Glance filter, detail, capability, and normalization parity | OPS-1901..1905 | Deferred — implementation committed; TMS/scope-policy closure pending |
| [OPS-1907](../tasks/sprint-19/OPS-1907-provider-parity-closure.md) | Close safe flavor/image lifecycle gaps with replay-safe SDK handlers | OPS-1906 | Planned |
| [OPS-1908](../tasks/sprint-19/OPS-1908-portal-live-acceptance.md) | CPS API/OPS/OpenStack comparison, recovery, and cleanup evidence | OPS-1907 | Planned |

## Architecture and reuse constraints

- Use OpenStackSDK connection/proxy/resource APIs. Horizon is a behavioral and
  test reference only; do not add novaclient, glanceclient, or Django.
- Reuse Horizon's normalized field names, status/format rules, policy cases,
  and edge-case tests when compatible with canonical CPS contracts.
- Never implement flavor update as delete then create.
- Never accept image bytes, tokens, signed URLs, or private source credentials
  on RabbitMQ. Unsupported data-plane requests fail before provider mutation.
- Every handler validates scope/capability, performs provider-state replay
  checks, uses bounded retries/waiters, normalizes safe request IDs/errors, and
  publishes terminal result before acknowledging the command.

## Required execution protocol

Every linked OPS task contains its own tailored workflow and must invoke the
installed skills in this order:

1. `superpowers:using-superpowers`, optional `superpowers:brainstorming` when a
   design choice remains, then `superpowers:writing-plans`; Codex ChatGPT 5.6
   sol saves an exact micro-plan under `docs/superpowers/plans/`.
2. `codex-security:threat-model` before code; new provider mutations, metadata,
   URL handling, access changes, and deletion need explicit abuse cases.
3. `superpowers:using-git-worktrees`, then
   `superpowers:subagent-driven-development` (preferred) or
   `superpowers:executing-plans`.
4. Cursor Composer 2.5 Fast uses `superpowers:test-driven-development` with an
   observed RED test before minimal implementation and refactor.
5. `superpowers:requesting-code-review`; Codex ChatGPT 5.6 luna performs the
   independent failure/security review. Worker applies
   `superpowers:receiving-code-review`, fixes verified findings, and Reviewer
   rechecks.
6. `codex-security:security-diff-scan` runs on the task Git diff through threat
   model, discovery, validation, and attack-path analysis as applicable.
   Reportable findings are triaged/fixed/tracked; unresolved Critical/High blocks
   live acceptance and Git completion.
7. `superpowers:verification-before-completion`, focused/full gates, CPS `curl`,
   OpenStack CLI comparison, zero-residual cleanup, and redacted runbook.
8. `superpowers:finishing-a-development-branch`, then separate task-scoped
   commit/push only with explicit Git authorization in that execution turn.

## Acceptance and evidence

- CPS and OPS contract checksums match.
- Duplicate/redelivered commands converge without duplicate resources.
- 401/403/404/409/429/5xx, timeout, unsupported capability, publish failure,
  and worker restart have deterministic outcomes.
- Every story has a live CPS API/OpenStack CLI field comparison and zero
  residual disposable resources.
- Runbook paths, test results, commit hashes, and pushed refs are recorded here.

## Review evidence

- Contract checksum:
- Test results:
- Live comparisons:
- Cleanup ledger:
- Runbooks:
- CPS/OPS commit and pushed refs:
