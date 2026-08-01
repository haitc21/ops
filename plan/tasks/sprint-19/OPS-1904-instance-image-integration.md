# OPS-1904 — Instance image snapshot and compatibility adapter

**Status:** Proposed  
**Points:** 5  
**Paired task:** CPS-1904  
**Depends on:** OPS-1901, OPS-1903, OPS-1701

## Outcome

OPS creates Nova server snapshots replay-safely and returns normalized Glance
image evidence for CPS reconciliation.

## Deliverables

- Pin CPS-1904 contract and implement the typed instance snapshot handler.
- Validate server ownership/state and image service capability before mutation.
- Use supported OpenStackSDK server image/snapshot API; discover an existing
  result on redelivery via operation marker/provider state before creating.
- Bound queued-to-terminal polling; normalize the resulting image and request a
  targeted image refresh. Do not download image data.
- Preserve existing resize/rebuild/create handlers; enforce provider-side
  preconditions where CPS compatibility could be stale.

## Tests first

- SDK tests for active/stopped/error server states, duplicate/redelivery,
  queued/active/killed image, quota/403/404/409/429/5xx, timeout, publish failure,
  worker restart, and unsupported image service.
- Contract/messaging tests for validation-before-mutation, no duplicate image,
  safe errors, confirmed terminal publish, and ack ordering.

## AI/Superpowers workflow

**Mandatory skill chain:** `superpowers:using-superpowers` →
`superpowers:writing-plans` → `codex-security:threat-model` →
`superpowers:using-git-worktrees` →
`superpowers:subagent-driven-development` or `superpowers:executing-plans` →
`superpowers:test-driven-development` →
`superpowers:requesting-code-review` / `superpowers:receiving-code-review` →
`codex-security:security-diff-scan` →
`superpowers:verification-before-completion` → live curl/CLI/runbook →
`superpowers:finishing-a-development-branch`. Use brainstorming if marker or
snapshot-state semantics remain undecided. Critical/High findings block completion.

1. **Planner — Codex ChatGPT 5.6 sol:** inspect CodeGraph create/action handlers,
   waiter/replay helpers, pinned CPS contract, Horizon snapshot semantics, and
   supported SDK APIs; define marker/search strategy, tests, live flow, cleanup.
2. **Worker — Cursor Composer 2.5 Fast:** pin contract, add failing SDK/messaging
   tests, implement with current connection/waiter/error/inventory helpers, and
   run focused tests before refactor.
3. **Reviewer — Codex ChatGPT 5.6 luna:** verify replay cannot create duplicate
   images, deadlines and terminal states, ownership/capability, normalized
   result, publish/ack order, and no image bytes or secrets.
4. Worker fixes findings, reruns suites, and Reviewer rechecks.

## Verification, runbook, and Git gate

Run all OPS gates; execute paired CPS snapshot `curl`; verify image and source
server with OpenStack CLI; use the snapshot through CPS; clean snapshot/server
and prove absence by CLI and CPS refresh. Add OPS evidence to the paired runbook.
After explicit authorization, commit/push OPS-1904 alone and record hashes/refs.

## Done when

Replay/recovery tests and live snapshot/use/cleanup pass, runbook is complete,
and task commits are pushed.
