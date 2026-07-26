# Sprint 9 — Internal network topology control

**Status:** Complete — physical home-LAN provider-network acceptance verified
**Dates:** 2026-07-24 to 2026-08-07  
**Capacity:** 40 OPS points  
**Sprint Goal:** Manage OpenStack Neutron resources required for VM access from
the corporate LAN, with explicit scope gates and normalized relationship results.

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

- [x] Confirm corporate-LAN connectivity as the primary acceptance target.
- [x] Add Neutron inventory collectors and primitive-only mappers.
- [x] Implement network/subnet/router/interface handlers.
- [x] Implement port/security-group/rule handlers.
- [x] Implement optional floating-IP allocate/associate/release handlers.
- [x] Normalize dependency conflicts, policy errors, and already-absent results.
- [x] Add replay/redelivery and topology cleanup tests.
- [x] Run physical home-LAN connectivity acceptance with a VM port.
- [x] Run Definition of Done quality gates and update evidence.

## Acceptance

- Handlers use supported OpenStackSDK Neutron APIs and never emit SDK objects.
- Project scope is sufficient for tenant topology; shared/external/provider-network operations are capability-gated.
- A VM receives a floating IP or provider-network IP that is reachable from the corporate LAN and is returned to CPS.
- Relationship operations are idempotent and safe under duplicate delivery.
- Floating IP is required unless the provider network directly assigns a corporate-LAN address.

## Risks and impediments

| Risk/impediment | Owner | Mitigation | Status |
|---|---|---|---|
| Neutron policy differs by deployment | OPS | Capability-gate shared/external operations and normalize 403 | Open |
| Provider dataplane must remain attached to the physical home bridge after reboot | OPS | Persist NetworkManager `br-home`, libvirt `provider-net` bridge mode, and controller/compute provider NIC attachment | Resolved for current lab |
| Eventual consistency after port/interface mutation | OPS | Bounded polling and replay-safe relationship operations | Open |

## Review evidence

- Demo scenario: Neutron topology operations return normalized resources plus a LAN-reachable floating/provider-network address.
- Test commands and results: OPS `372 passed, 24 skipped`; network focused tests, Ruff, mypy, and contract validation passed.
- CPS checksum: network operations map to the pinned generic resource-operation envelope.
- Physical-LAN result: provider network was moved to `br-home` on `enp8s0`, Neutron subnet changed to `192.168.0.0/24`, and a live CirrOS VM received provider IP `192.168.0.240`; host wired/Wi-Fi paths, controller, and compute reached it by ping and TCP/22. The VM, port, and temporary security group were deleted after the test.
- Runtime evidence: OpenStack provider interfaces on controller and compute are attached to the physical bridge; Nova compute uses the nested-lab-compatible QEMU CPU configuration.

## Retrospective actions

- Keep: provider-neutral resource mapping and project ownership checks.
- Improve: live Neutron acceptance from the Compose network.
- One measurable action for next sprint: add a private-IP SSH smoke test after VM completion.
