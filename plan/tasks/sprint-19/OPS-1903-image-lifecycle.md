# OPS-1903 — Image metadata, access, and lifecycle handlers

**Status:** Blocked — no configured allowlisted HTTPS lab source
**Points:** 13  
**Paired task:** CPS-1903  
**Depends on:** OPS-1901

## Outcome

OPS safely performs supported Glance image operations and converges asynchronous
or duplicate delivery without carrying binary image data or source credentials.

## Deliverables

- Pin CPS-1903 contracts and register typed create/import, metadata/visibility/
  protection, member, deactivate/reactivate, and delete handlers.
- Use supported `connection.image` APIs and capability discovery; do not add
  glanceclient or direct HTTP calls.
- Validate the command before credential resolution/provider mutation. Reject
  binary fields and credential-bearing URLs defensively even if CPS validated.
- Create/import replay locates the existing image by immutable operation marker
  or deterministic provider state; status waiter is bounded and distinguishes
  queued/importing/active/killed/deactivated.
- Metadata/member changes compute deltas and tolerate already-converged state.
  Protected delete conflicts; absent delete is idempotent success.
- Normalize safe fields/errors/request IDs, request targeted refresh, publish
  terminal result with confirms, then ack.

## Tests first

- SDK fake tests for each operation and state, duplicate create/import, member
  partial failure, metadata removal, protected image, unsupported API, 401/403/
  404/409/429/5xx, timeout, and malformed/secret-bearing URL.
- Messaging tests for validation-before-mutation, bounded retry, publish failure,
  redelivery/restart, DLQ, and credential/log redaction.

## AI/Superpowers workflow

**Mandatory skill chain:** `superpowers:using-superpowers` →
`superpowers:brainstorming` → `superpowers:writing-plans` →
`codex-security:threat-model` → `superpowers:using-git-worktrees` →
`superpowers:subagent-driven-development` or `superpowers:executing-plans` →
`superpowers:test-driven-development` →
`superpowers:requesting-code-review` / `superpowers:receiving-code-review` →
`codex-security:security-diff-scan` →
`superpowers:verification-before-completion` → live curl/CLI/runbook →
`superpowers:finishing-a-development-branch`. Brainstorming and threat modeling
are mandatory for URL/import/member/delete paths. Critical/High findings block
completion.

1. **Planner — Codex ChatGPT 5.6 sol:** query CodeGraph for handler/registry/
   retry/discovery flows, read pinned CPS contracts and Horizon Glance behavior,
   verify current OpenStackSDK APIs, and define replay marker, state machine,
   call deadlines, red tests, live commands, and cleanup.
2. **Worker — Cursor Composer 2.5 Fast:** pin contracts, write failing SDK and
   messaging tests, implement operation slices with current factory/retry/error/
   waiter/inventory helpers, and retain credentials only in bounded memory.
3. **Reviewer — Codex ChatGPT 5.6 luna:** inspect SDK support, URL defense,
   absence of bytes/secrets, asynchronous convergence, member/metadata deltas,
   protected/status checks, duplicate behavior, publish/ack ordering, and logs.
4. Worker fixes findings and reruns focused/full suites; Reviewer rechecks.

## Verification, runbook, and Git gate

Run OPS contract/unit/messaging/full gates and secret scan. Execute paired CPS
`curl` operations, verify each state/property/member through OpenStack CLI, and
confirm final deletion and zero residual resources. Add redacted OPS evidence to
`cps/docs/runbooks/sprint-19-image-lifecycle.md`. After explicit authorization,
commit/push OPS-1903 alone and record both repository hashes/refs.

## Done when

All deterministic and live checks pass, unsupported clouds fail explicitly,
cleanup is complete, runbook exists, and task commits are pushed.
