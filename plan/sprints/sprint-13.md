# Sprint 13 — Provider resolution and authorization decision propagation

**Status:** Ready for implementation  
**Dates:** 2026-09-19 to 2026-10-02  
**Capacity:** 13 OPS points  
**Sprint Goal:** OPS resolves provider access without a credential identifier and executes only commands carrying a valid, secret-safe CPS authorization decision context.

**Canonical CPS design:**  
`../../../cps/docs/superpowers/specs/2026-07-26-provider-tenancy-authorization-design.md`

**Repository constraint:** OPS and the pinned CPS contract are the only OPS delivery scope. OPS must not call or modify TMS/LMS.

## Selected stories

| Story | Points | Owner | CPS dependency | Status |
|---|---:|---|---|---|
| OPS-1201 Provider-owned credential contract | 8 | OPS | CPS-1201, CPS-1202 | Ready |
| OPS-1202 Authorization decision command context | 5 | OPS | CPS-1203 | Ready |

## Delivery tasks

### OPS-1201 — Provider-owned credential contract

- [ ] Pin CPS schemas and fixtures that remove `credential_reference`.
- [ ] Resolve provider access through `provider_connection_id` only.
- [ ] Reject legacy/malformed commands before OpenStack access.
- [ ] Preserve project owner identifiers in normalized resource payloads.
- [ ] Map `location.project.id`, `project_id`, and `tenant_id` consistently for all tenant-owned collectors and operation results.
- [ ] Verify SDK objects, usernames, passwords, tokens, and decrypted secrets do not escape adapter boundaries.
- [ ] Add replay and compatibility tests for system/project-scoped connections.

### OPS-1202 — Authorization decision context

- [ ] Pin the safe authorization decision metadata contract from CPS.
- [ ] Reject user-originated commands missing required decision context.
- [ ] Propagate decision and correlation IDs to safe logs/results without JWT, roles, or secret material.
- [ ] Keep system reconciliation commands explicitly distinguished from user-initiated commands.
- [ ] Prove OPS performs no TMS/LMS network call and makes no independent workspace-role decision.
- [ ] Add duplicate, expired-at-dispatch, malformed-context, and redaction tests.

## Acceptance

- OPS command contracts contain no credential ID/reference.
- OpenStack connection resolution starts from `provider_connection_id`.
- Every normalized tenant resource includes its OpenStack project owner when the provider supplies one.
- User commands without valid CPS authorization decision context fail before provider mutation.
- OPS does not receive bearer tokens and does not call TMS or LMS.
- Contract checksum, format, lint, typing, unit, integration, replay, redaction, and build gates pass.

## Risks and impediments

| Risk/impediment | Owner | Mitigation | Status |
|---|---|---|---|
| Legacy queued commands contain credential references | CPS/OPS | Version contract, reject unsupported legacy commands explicitly, drain queue before rollout | Open |
| SDK services expose project ownership under different fields | OPS | Central owner mapper with fixture coverage for each service | Open |
| Authorization context could be mistaken for OPS policy authority | OPS | Treat it as CPS-issued execution precondition; never evaluate tenant roles in OPS | Open |

## Review evidence

- Contract checksum:
- Provider resolution and redaction:
- Project-owner normalization:
- Authorization-context rejection/replay:
- Verification that TMS/LMS have no diff:

## Retrospective actions

- Keep:
- Improve:
- One measurable action for the next sprint:
