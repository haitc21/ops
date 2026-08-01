# OPS-1902 — Flavor lifecycle handlers

**Status:** Proposed  
**Points:** 8  
**Paired task:** CPS-1902  
**Depends on:** OPS-1901

## Outcome

OPS executes Nova flavor create/delete/access/extra-spec commands safely and
converges duplicate deliveries against provider state.

## Deliverables

- Pin CPS-1902 contracts/checksum and register typed flavor handlers.
- Implement create, delete, replace access, and patch extra specs with supported
  `connection.compute` APIs.
- Before create/retry, resolve by requested provider ID and deterministic
  operation marker/name rules; reject conflicting shape rather than duplicate.
- Access replacement computes add/remove deltas and tolerates already-present/
  absent membership. Extra-spec patch applies removals and updates explicitly.
- Delete checks provider state/dependencies where available; already absent is
  idempotent success. Never emulate update by delete/create.
- Bound SDK calls/retries, normalize Nova errors/request IDs, publish terminal
  result before ack, and request targeted refresh.

## Tests first

- Mocked SDK tests for every happy path, duplicate/redelivery, conflicting
  existing flavor, access partial failure, extra-spec partial failure, 404,
  409, 429, 5xx, timeout, publish failure, and unsupported capability.
- Messaging tests for validation-before-mutation, ack order, retry/DLQ, restart,
  and no duplicate flavor.

## AI/Superpowers workflow

**Mandatory skill chain:** `superpowers:using-superpowers` →
`superpowers:writing-plans` → `codex-security:threat-model` →
`superpowers:using-git-worktrees` →
`superpowers:subagent-driven-development` or `superpowers:executing-plans` →
`superpowers:test-driven-development` →
`superpowers:requesting-code-review` / `superpowers:receiving-code-review` →
`codex-security:security-diff-scan` →
`superpowers:verification-before-completion` → live curl/CLI/runbook →
`superpowers:finishing-a-development-branch`. Use brainstorming for unresolved
replay or destructive-delete policy. Critical/High findings block completion.

1. **Planner — Codex ChatGPT 5.6 sol:** inspect handler registry, retry/error/
   waiter code and SDK APIs through CodeGraph; compare Horizon semantics; define
   replay preconditions, delta algorithm, tests, live sequence, and cleanup.
2. **Worker — Cursor Composer 2.5 Fast:** pin contracts, write failing handler
   and messaging tests, implement one operation at a time using current factory,
   retry, error, and inventory helpers; keep credentials in memory only.
3. **Reviewer — Codex ChatGPT 5.6 luna:** verify supported SDK use, replay safety,
   partial access/spec convergence, deadlines, publish-before-ack, error
   normalization/redaction, and prohibition on flavor replace-by-delete.
4. Worker fixes findings and reruns focused/full suites; Reviewer re-approves.

## Verification, runbook, and Git gate

Run all OPS gates, then execute the paired CPS-1902 `curl` flow and independently
verify create/access/spec/delete with OpenStack CLI. Record redacted OPS logs,
provider request IDs, retries, comparisons, and zero-residual cleanup in the
paired runbook. After explicit Git authorization, commit/push only OPS-1902 and
record both repository hashes and refs.

## Done when

Handler/messaging tests and live CPS-to-Nova verification pass without duplicate
or residual flavor, runbook is complete, and task commits are pushed.
