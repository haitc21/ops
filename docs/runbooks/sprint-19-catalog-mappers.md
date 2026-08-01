# Sprint 19 — OPS-1901 catalog mapper evidence

**Status:** Done
**Branch:** `sprint-19/ops-1901`
**Canonical CPS commit:** `b8a5ff7`

## Scope and contract pin

OPS pins the CPS 1.1 inventory schema, capability schema, four image/flavor
fixtures, the shared safe-metadata rules, and manifest entries. Validation
reported 20 artifacts and the CPS pin suite passed all five checks. No image
bytes, provider credentials, service-catalog endpoints, or raw SDK bodies are
persisted or emitted.

The approved review correction permits one bounded `image.members(image)` call
for each shared image because OpenStackSDK image list resources do not carry
member IDs. Public, private, and community images make no membership call.
Flavor specs/private access and shared-image membership consume the configured
enrichment budget before provider access.

## RED/GREEN and review evidence

- Baseline: 27 focused tests passed before implementation.
- RED: missing pinned schema/fixtures, absent catalog fields and capability
  keys, schema 1.0 enriched events, post-get 404 tombstones, and zero real
  image-membership calls all failed for the expected reasons.
- GREEN: full suite `491 passed, 24 skipped`; Ruff passed; MyPy passed for 60
  production files; 20 contract artifacts validated; CPS byte pin `5 passed`;
  `git diff --check` passed.
- Reviewer cycle 1 found three High and three Medium groups; all received
  regression tests and technical fixes.
- Reviewer cycle 2 verified the supported membership API, shared-only call
  rule, pre-call budget, deterministic bounds, failure propagation, format and
  visibility matrix. Final decisions: `PASS1=PASS`, `PASS2=PASS`, no findings.
- Dedicated Codex Security plugin gates were removed by direct user instruction.
  Repository secret scanning remains a completion gate.

## Live provider comparison

The host Python worker was used to avoid Docker DNS indirection. CPS operation
`019fbe70-105a-7e7b-8a86-f0030e138204` reached `SUCCEEDED` after one poll for
project-scoped connection `019fb26d-6faa-7a0f-a80d-a782f651ec8a`.

Independent `openstack` CLI output on the controller and CPS administrator
catalog projections matched exactly:

| Resource | Count | Provider-ID set | Material fields |
|---|---:|---|---|
| Image | 2 | match | name, status, visibility, disk format, size all match |
| Flavor | 3 | match | name, RAM, root disk, vCPU, public flag all match |

Connection validation operation `019fbe70-a6c3-7c0e-a949-0abb22bc15d4`
reached `SUCCEEDED`. Persisted lifecycle capabilities reported image member,
deactivate, reactivate and all four flavor operations supported; image import
reported `CAPABILITY_NOT_SUPPORTED` rather than false support.

System-scoped connection `019fb259-cc0b-726a-b98a-22dcc24734b7` produced a
successful partial sync with image `SKIPPED_UNSUPPORTED` after Glance returned
403, while flavor completed. This verifies that forbidden image collection
does not publish a misleading empty `COMPLETE` batch or delete prior inventory.

## Cleanup and limitations

OPS-1901 is read-only. Pre/post provider sets remained two images and three
flavors; no provider resource required cleanup. OPS ran from the worktree and
created no local cache. The system-scoped Glance policy limitation is retained
as negative evidence; project-scoped acceptance is authoritative for this lab.

Credentials, bearer tokens, authorization headers, private material, unsafe
metadata, and raw provider bodies are intentionally omitted.
