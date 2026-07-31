# OPS-1201 — Provider-owned credential and project-owner contract

**Status:** Done
**Active backlog:** No — connection-only resolution, project-owner
normalization, contract parity, and redaction gates are complete.
**Points:** 8  
**Depends on:** CPS-1201, CPS-1202  
**Design:** `../../../../cps/docs/superpowers/specs/2026-07-26-provider-tenancy-authorization-design.md`

## Outcome

OPS resolves OpenStack access by provider connection without a credential
identifier and emits project ownership for every tenant resource supported by
the provider.

## Tasks

1. Pin CPS schemas, fixtures, and checksum without `credential_reference`.
2. Refactor validation/resolution to accept `provider_connection_id` only.
3. Reject legacy or malformed commands before constructing an SDK connection.
4. Add one owner-project mapper with precedence:
   `location.project.id`, `project_id`, `tenant_id`.
5. Apply the mapper to compute, volume, network, subnet, port, router, security,
   floating-IP, image, quota, and operation-result adapters.
6. Preserve nullable ownership for global/shared provider resources.
7. Add system/project-scoped connection compatibility and replay tests.
8. Verify SDK objects and all credential/token material remain adapter-local.

## Acceptance tests

- No command schema contains a credential ID/reference.
- Resolver cannot select a credential independently of provider connection.
- Every tenant mapper emits `project_provider_resource_id` when supplied by
  OpenStack.
- Conflicting owner fields reject or follow the documented deterministic rule.
- Duplicate delivery emits the same normalized ownership.
- Logs/results contain no username, password, token, catalog, or SDK object.

## Verification

```bash
uv run ruff check .
uv run mypy src
uv run pytest -q
```

## Out of scope

- SQL/database ownership.
- TMS/LMS calls or code changes.
- Tenant role evaluation.
