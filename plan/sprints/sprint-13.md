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

## Task backlog

| Task | Deliverable | Depends on | Status |
|---|---|---|---|
| [OPS-1201](../tasks/sprint-13/OPS-1201-provider-project-contract.md) | Connection-only provider resolution and normalized project ownership | CPS-1201, CPS-1202 | Ready |
| [OPS-1202](../tasks/sprint-13/OPS-1202-authorization-context.md) | Safe authorization context validation and replay behavior | CPS-1203, OPS-1201 | Ready |

## Execution sequence

1. Pin CPS-1201/1202 contracts and complete OPS-1201.
2. Pin the CPS-1203 authorization context and complete OPS-1202.
3. Run joint checksums, replay, expiry, owner-normalization, and redaction gates.
4. Verify OPS makes no TMS/LMS call and those repositories remain untouched.

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
