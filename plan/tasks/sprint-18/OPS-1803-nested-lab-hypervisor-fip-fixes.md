# OPS-1803 — Nested lab hypervisor, FIP ops, and inventory fixes

**Status:** Done
**Active backlog:** No — OPS fixes, FIP association, durable KVM operation,
multi-compute migration, TCP/22 verification, and cleanup are complete.
**Points:** TBD
**Paired task:** CPS-1803

## Outcome

OPS reliably creates runnable instances on nested OpenStack lab compute and
executes floating IP associate without misreported errors or QEMU tcg crashes.

## Scope

### Delivered code

- `ops/src/ops/openstack/inventory.py` — map `flavor.extra_specs.cmp-catalog-approved` → `catalog_approved`
- `ops/src/ops/application/handlers/instance_create.py` — resolve security group UUID to Nova `[{"name": "..."}]`
- Tests: `ops/tests/unit/openstack/test_inventory.py`, `ops/tests/unit/application/test_instance_create.py`

### Bugs to fix

1. **Floating IP associate** — investigate CPS → OPS payload and Neutron `update_ip(port_id=...)`; fix wrong `provider_service: identity` on failure.
2. **Nested KVM domain type** — Nova/libvirt on compute01 must use `kvm` acceleration, not `tcg` (see CPS-1803 H1, H2).
3. **Error normalization** — ensure network resource operations report `network` service, not default `identity`.

## Lab configuration notes (compute01)

Correct `[libvirt]` section in `/etc/nova/nova.conf`:

```
cpu_mode = host-model
hw_machine_type = pc-i440fx-6.2
virt_type = kvm
use_usb_tablet = false
vnc_enabled = false   # under [DEFAULT] or [vnc]
```

Temporary hook (remove after permanent fix): `/etc/libvirt/hooks/qemu` — rewrite `type='qemu'` to `type='kvm'` on prepare.

## Done when

- [x] OPS fixes are covered by the full green unit/integration gates.
- [x] FIP associate integration test passes against lab Neutron (CPS op SUCCEEDED 2026-07-29).
- [x] Durable KVM compute operation is verified; compute01 and compute02 are
  enabled/up and cold migration works in both directions with TCP/22 preserved.
- [x] Documented in lab runbook or sprint-18 release notes if hook remains required short-term.

Provider-authoritative `cmp180-*` cleanup returned zero residuals. Final live
evidence is recorded in CPS/OPS-1802.
