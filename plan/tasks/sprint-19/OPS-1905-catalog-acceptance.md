# OPS-1905 — Image/flavor adapter acceptance and cleanup

**Status:** Blocked — live release matrix incomplete
**Points:** 3  
**Paired task:** CPS-1905  
**Depends on:** OPS-1901..1904

## Outcome

OPS image/flavor handlers are proven compatible, replay-safe, bounded, and
clean on the target OpenStack environment.

## Deliverables

- Run the full handler, contract, messaging, retry/DLQ, restart, and capability
  matrix for Sprint 19.
- Exercise one supported and, where available, one explicitly unsupported
  capability path without release-name assumptions.
- Execute paired CPS curl scenarios and independently verify every material
  provider state with OpenStack CLI.
- Capture safe provider request IDs and timing/error summaries; capture no
  token, credential, signed URL, raw image, or unbounded provider body.
- Clean all `cmp-s19-*` resources and verify absence after full reconciliation.

## AI/Superpowers workflow

**Mandatory skill chain:** `superpowers:using-superpowers` →
`superpowers:writing-plans` → `codex-security:threat-model` →
`superpowers:using-git-worktrees` →
`superpowers:subagent-driven-development` or `superpowers:executing-plans` →
`superpowers:test-driven-development` for acceptance-code changes →
`superpowers:requesting-code-review` / `superpowers:receiving-code-review` →
`codex-security:security-diff-scan` →
`superpowers:verification-before-completion` → live curl/CLI/runbook →
`superpowers:finishing-a-development-branch`. Final release evidence includes
the security scan report and closure ledger; Critical/High findings block release.

1. **Planner — Codex ChatGPT 5.6 sol:** derive the OPS matrix from handlers,
   CodeGraph blast radius, CPS-1905, and prior task runbooks; specify failure
   injection, curl/CLI assertions, cleanup order, and commit boundaries.
2. **Worker — Cursor Composer 2.5 Fast:** fill only missing test/script evidence,
   execute focused/full/live gates, and maintain an ID-based cleanup ledger.
3. **Reviewer — Codex ChatGPT 5.6 luna:** audit replay/deadline/publish-before-ack
   evidence, capability behavior, log redaction, CLI equivalence, cleanup, and
   checksum identity; return concrete findings.
4. Worker resolves findings and reruns; Reviewer gives final approval.

## Verification, runbook, and Git gate

Use the consolidated CPS runbook `docs/runbooks/sprint-19-image-flavor-release.md`
as the source of exact curl/CLI commands and link OPS logs/test outputs from it.
Run Ruff, MyPy, full OPS tests, contract validation, diff check, and secret scan.
After explicit Git authorization, commit/push OPS-1905 separately and record CPS
and OPS hashes, remote refs, and clean worktree evidence.

## Done when

The full matrix passes or has an explicit approved environmental limitation,
cleanup is zero-residual, release runbook is complete, and commits are pushed.
