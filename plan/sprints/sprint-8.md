# Sprint 8 — Identity lifecycle, role assignments, and quotas

**Status:** Complete — live disposable identity lifecycle and cleanup verified
**Dates:** 2026-07-24 to 2026-08-07  
**Capacity:** 21 OPS points  
**Sprint Goal:** Safely execute OpenStack Keystone identity mutations and quota
operations with explicit scope authorization and replay-safe events.

**Canonical CPS plan:**
`../../../cps/docs/superpowers/plans/2026-07-24-openstack-resource-control-plane-expansion.md`

## Selected stories

| Story | Points | Owner | CPS dependency | Status |
|---|---:|---|---|---|
| OPS-801 Domain/project handlers | 5 | OPS | CPS-801 | Done |
| OPS-802 Role assignment handlers | 5 | OPS | CPS-802 | Done |
| OPS-803 Quota collectors/handlers | 5 | OPS | CPS-803 | Done |
| OPS-804 Identity real-cloud acceptance | 6 | OPS/CPS | CPS-804 | Done |

## Delivery tasks

- [x] Confirm CPS Sprint 7 contracts and scope discovery.
- [x] Add lifecycle, assignment, and quota command/event contracts.
- [x] Implement domain/project create/update/disable/delete handlers.
- [x] Implement role assignment ensure/revoke handlers.
- [x] Implement compute/network/block-storage quota collectors and updates.
- [x] Normalize policy errors, dependency conflicts, and already-absent results.
- [x] Add replay/redelivery and provider cleanup tests.
- [x] Run real OpenStack disposable identity acceptance.
- [x] Run Definition of Done quality gates and update evidence.

## Acceptance

- Handlers use only supported OpenStackSDK APIs and primitive JSON events.
- Every mutation is gated by effective scope and capability, never by username.
- Role assignment and quota operations are idempotent and retry-safe.
- Provider 401/403/timeout outcomes never imply deletion.
- Cleanup is verified after lifecycle acceptance.

## Risks and impediments

| Risk/impediment | Owner | Mitigation | Status |
|---|---|---|---|
| Provider catalog endpoints are not routable from Compose | OPS | Add endpoint preflight before real-cloud acceptance | Open |
| Keystone role/policy varies by deployment | OPS | Normalize authorization failures and capability reasons | Open |
| Service quota APIs vary by OpenStack release | OPS | Use adapter matrix and partial-service result model | Open |

## Review evidence

- Demo scenario: OPS resolved a system-scoped OpenStack connection and executed
  domain/project create, update-disable, and delete commands from CPS.
- Test commands and results: OPS full suite, resource-operation/contract tests,
  Ruff, mypy, and contract checksum validation passed.
- CPS checksum: generic resource-operation contract remains pinned and byte-identical.
- Real-cloud lifecycle/cleanup result: disposable domain `codex-domain-11` and
  project `codex-project-11` reached terminal success and were absent after
  cleanup on the live controller.
- Known limitations: role/quota live mutation acceptance and public floating-IP
  acceptance remain deployment-specific follow-up work.

## Retrospective actions

- Keep: secret rejection, capability gating, and normalized provider-neutral events.
- Improve: deploy-time service catalog reachability diagnostics.
- One measurable action for next sprint: verify SSH connectivity to the private address returned in the instance completion event.
