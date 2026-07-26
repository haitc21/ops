# OPS-1202 — Authorization decision command context

**Status:** Ready  
**Points:** 5  
**Depends on:** CPS-1203, OPS-1201  
**Design:** `../../../../cps/docs/superpowers/specs/2026-07-26-provider-tenancy-authorization-design.md`

## Outcome

OPS executes user-initiated commands only when CPS supplies valid, secret-safe
authorization decision metadata. OPS does not receive a JWT, call TMS/LMS, or
evaluate tenant roles.

## Tasks

1. Pin the canonical safe authorization context and checksum from CPS.
2. Distinguish user commands from system reconciliation commands explicitly.
3. Validate decision ID, subject ID, org/workspace, permission, and expiry for
   user commands before provider access.
4. Reject missing, malformed, mismatched, and expired contexts.
5. Propagate only decision/correlation IDs into safe structured logs and
   normalized results.
6. Keep replay deterministic and prevent expired redelivery from mutating the
   provider.
7. Add a test proving no OPS adapter imports or calls a TMS/LMS client.

## Acceptance tests

- Valid CPS decision context reaches the intended handler.
- Missing/malformed/expired context fails before SDK connection construction.
- System reconciliation requires its explicit command kind and cannot be forged
  as a user command without contract validation.
- Duplicate valid delivery remains replay-safe.
- JWTs, role lists, and service credentials are rejected by schema/redaction
  tests.
- TMS and LMS repositories remain untouched.

## Verification

```bash
uv run ruff check .
uv run mypy src
uv run pytest -q
```

## Out of scope

- Calling TMS or LMS.
- Caching or evaluating user roles.
- Persisting authorization decisions in OPS.
