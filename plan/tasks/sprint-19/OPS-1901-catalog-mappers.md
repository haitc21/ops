# OPS-1901 — Catalog mapper and capability enrichment

**Status:** Done — automated and live acceptance passed
**Points:** 5  
**Paired task:** CPS-1901  
**Depends on:** OPS-1703

## Outcome

OPS maps complete, bounded image/flavor inventory and reports the capabilities
required by later lifecycle handlers without leaking SDK objects or secrets.

## Deliverables

- Pin CPS-1901 schemas, fixtures, and checksum without provider-specific drift.
- Enrich `openstack/inventory.py` mappers and targeted collectors with canonical
  image/flavor fields, safe extra specs, project access, tags, and properties.
- Preserve generator pagination and stable ordering; avoid per-row calls where
  bulk enrichment is available and bound unavoidable N+1 calls.
- Discover capabilities for image import/member/deactivate/reactivate and
  flavor create/delete/access/extra specs. Report explicit unsupported reasons,
  not named OpenStack release checks.
- Sanitize nested metadata and reject secret-bearing keys/values.

## Tests first

- SDK-resource mapper tests for missing/optional fields, unusual swap values,
  Docker/raw formats, private/shared/community visibility, tags/properties,
  access lists, large metadata, and sensitive keys.
- Collector tests for pagination, partial enrichment failure, service absence,
  timeout, stable order, and targeted 404.
- Contract-pin and capability-document tests.

## AI/Superpowers workflow

**Mandatory skill chain:** `superpowers:using-superpowers` →
`superpowers:writing-plans` → `codex-security:threat-model` →
`superpowers:using-git-worktrees` →
`superpowers:subagent-driven-development` or `superpowers:executing-plans` →
`superpowers:test-driven-development` →
`superpowers:requesting-code-review` / `superpowers:receiving-code-review` →
`codex-security:security-diff-scan` →
`superpowers:verification-before-completion` → live curl/CLI/runbook →
`superpowers:finishing-a-development-branch`. Use brainstorming if mapper bounds
or capability semantics remain undecided. Critical/High findings block completion.

1. **Planner — Codex ChatGPT 5.6 sol:** inspect CodeGraph callers of
   `map_resource`, `collect_resources`, and capability discovery; read CPS-1901
   contracts and Horizon wrappers; produce exact SDK field mapping, call budget,
   failure cases, and live verification plan.
2. **Worker — Cursor Composer 2.5 Fast:** pin contracts, create failing mapper/
   collector tests, implement bounded enrichment/capabilities with supported
   OpenStackSDK APIs, then run focused tests before refactoring.
3. **Reviewer — Codex ChatGPT 5.6 luna:** verify checksum identity, no SDK/raw
   bodies cross the boundary, bounded calls/timeouts, optional-field handling,
   redaction, deterministic ordering, and no novaclient/glanceclient dependency.
4. Worker fixes findings and reruns affected/full suites; Reviewer rechecks.

## Verification, runbook, and Git gate

1. Run focused inventory/discovery tests, contract validation, Ruff, MyPy, full
   OPS tests, `rtk git diff --check`, and secret scan.
2. Trigger inventory using CPS `curl`; poll the operation and query CPS list/
   detail endpoints.
3. Run `openstack image list/show` and `openstack flavor list/show`, comparing
   IDs, fields, approval marker, and capabilities with CPS output.
4. Add OPS evidence to `../cps/docs/runbooks/sprint-19-catalog-contracts.md` or
   a linked OPS runbook; include no token or raw response body.
5. After explicit authorization, commit/push OPS-1901 separately and record
   branch, hash, remote ref, and clean status.

## Done when

Pinned contracts match CPS, deterministic tests pass, live inventory matches
CLI, runbook is complete, and the task commit is pushed.
