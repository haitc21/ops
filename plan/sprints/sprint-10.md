# Sprint 10 — OpenStack tenant binding and ownership

**Status:** In progress  
**Dates:** 2026-08-08 to 2026-08-21  
**Capacity:** 8 OPS points  
**Sprint Goal:** OPS can execute explicit OpenStack domain/project create
commands from CPS keyed by `provider_id` without adopting provider objects
implicitly from inventory.

**Canonical CPS design:**
`../../../cps/docs/superpowers/specs/2026-07-24-openstack-cmp-org-workspace-binding-spec.md`

## Selected stories

| Story | Points | Owner | CPS dependency | Status |
|---|---:|---|---|---|
| OPS-704 OpenStack domain/project create handlers | 8 | OPS | CPS-704 | In progress |

## Delivery tasks

- [x] Confirm CPS contract/checksum readiness for binding create commands keyed
  by `provider_id`.
- [x] Add failing handler and replay tests for domain and project creation.
- [x] Implement the smallest OpenStackSDK vertical slice for create-domain.
- [x] Implement the project create handler with domain dependency validation.
- [x] Normalize provider-side conflict and already-exists outcomes.
- [x] Verify logs, events, and terminal payloads remain secret-safe.
- [x] Run the Definition of Done quality gates.

## Acceptance

- OPS creates domains only from an explicit CPS create command that includes
  `provider_id`.
- OPS creates projects only when CPS supplies `provider_id`, `org_id +
  workspace_id`, and the matching domain binding context.
- OPS resolves the provider aggregate (encrypted credentials and connection
  metadata) from CPS; no separate credential or connection object crosses the
  handler boundary.
- Name-only matches never auto-adopt an unbound provider object.
- Duplicate delivery is idempotent and replay-safe.
- Scope insufficiency and provider collisions normalize to stable safe errors.
- No provider-side object or SDK object escapes the handler boundary.

## Risks and impediments

| Risk/impediment | Owner | Mitigation | Status |
|---|---|---|---|
| Keystone policies may reject system-level identity mutation | OPS | Scope-gate mutation before provider call and normalize explicit denial | Open |
| Existing provider objects may already exist with the same display name | OPS | Return conflict or already-exists without auto-adoption | Open |
| Replay detection must not create duplicate provider resources | OPS | Require explicit idempotent lookup or provider-side marker where supported | Open |

## Review evidence

- Demo scenario: explicit provider aggregate resolution and OpenStack connection
  validation passed; identity command was published with `provider_id`.
- Test/migration commands and results: OPS `365 passed, 24 skipped`; Ruff and
  mypy passed; CPS contract boundary remained secret-safe.
- Contract checksum:
- Known limitations: live identity command remained `QUEUED` without a terminal
  event, and project-scoped admin credentials cannot satisfy system mutation
  policy for domain creation.

## Retrospective actions

- Keep: explicit provider-side mutation boundaries.
- Improve: conflict reporting for name collisions versus true duplicates.
- One measurable action for next sprint: add a replay test that proves domain
  create does not run twice under redelivery.
