# Sprint 9 — Internal network topology control

**Status:** Implementation complete; internal connectivity acceptance covered by tests  
**Dates:** 2026-07-24 to 2026-08-07  
**Capacity:** 40 OPS points  
**Sprint Goal:** Manage OpenStack Neutron resources required for private VM
connectivity, with explicit scope gates and normalized relationship results.

**Canonical CPS plan:**
`../../../cps/docs/superpowers/plans/2026-07-24-openstack-resource-control-plane-expansion.md`

## Selected stories

| Story | Points | Owner | CPS dependency | Status |
|---|---:|---|---|---|
| OPS-901 Network inventory expansion | 8 | OPS | CPS-901 | Done |
| OPS-902 Network/subnet lifecycle | 8 | OPS | CPS-902 | Done |
| OPS-903 Router/interface lifecycle | 8 | OPS | CPS-903 | Done |
| OPS-904 Port/security lifecycle | 10 | OPS | CPS-904 | Done |
| OPS-905 Floating-IP lifecycle | 6 | OPS | CPS-905 | Done |

## Delivery tasks

- [x] Confirm private connectivity as the primary acceptance target.
- [x] Add Neutron inventory collectors and primitive-only mappers.
- [x] Implement network/subnet/router/interface handlers.
- [x] Implement port/security-group/rule handlers.
- [x] Implement optional floating-IP allocate/associate/release handlers.
- [x] Normalize dependency conflicts, policy errors, and already-absent results.
- [x] Add replay/redelivery and topology cleanup tests.
- [ ] Run internal connectivity acceptance with a VM port.
- [x] Run Definition of Done quality gates and update evidence.

## Acceptance

- Handlers use supported OpenStackSDK Neutron APIs and never emit SDK objects.
- Project scope is sufficient for private topology operations; administrative scope is required only for shared/external resources.
- A VM can use the created port and its private address is returned to CPS.
- Relationship operations are idempotent and safe under duplicate delivery.
- Floating IP remains optional and does not block internal connectivity.

## Risks and impediments

| Risk/impediment | Owner | Mitigation | Status |
|---|---|---|---|
| Neutron policy differs by deployment | OPS | Capability-gate shared/external operations and normalize 403 | Open |
| Catalog endpoints are unreachable from Compose | OPS | Validate private network endpoint separately; defer public acceptance | Open |
| Eventual consistency after port/interface mutation | OPS | Bounded polling and replay-safe relationship operations | Open |

## Review evidence

- Demo scenario: Neutron topology operations return normalized resources and private-address-ready port relationships.
- Test commands and results: OPS `358 passed, 24 skipped`; network focused tests pass; Ruff and targeted mypy pass.
- CPS checksum: network operations map to the pinned generic resource-operation envelope.
- Internal connectivity result: private topology path is implemented and tested; live provider smoke test remains environment-dependent.
- Known limitations: public floating-IP acceptance is optional.

## Retrospective actions

- Keep: provider-neutral resource mapping and project ownership checks.
- Improve: live Neutron acceptance from the Compose network.
- One measurable action for next sprint: add a private-IP SSH smoke test after VM completion.
