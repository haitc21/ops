# OPS-1906 — Horizon semantic parity and catalog enrichment

**Status:** Deferred — implementation committed; CPS/OPS-1909 re-review pending  
**Points:** 8  
**Paired task:** CPS-1906
**Depends on:** OPS-1909, CPS-1909

## Testable outcome

OPS supplies the Nova/Glance fields, filters, access data, capability reasons,
and normalized states needed by the CPS API contract with bounded provider
calls and no Horizon runtime dependency.

## Deliverables and tests

- Use Horizon `api/nova.py`, `api/glance.py`, tables/forms/views/tests as an
  Apache-2.0 behavioral/test source; document provenance and every adaptation.
- Extend `src/ops/openstack/inventory.py`, discovery/capability mapping, pinned
  CPS contracts/fixtures/checksums, and focused inventory/discovery tests only
  where the gap matrix proves a missing field or semantic.
- RED cases cover pagination/filter normalization, private flavor access,
  image members/properties/tags/status/visibility, bounded enrichment, missing
  SDK methods, 401/403/404/429/5xx, and sanitized errors.
- No Django, novaclient, glanceclient, unbounded N+1 lookup, credentials, image
  bytes, or raw provider body crosses OPS output.

## Verification and commit

Run contract/inventory/discovery/full OPS gates and checksum/secret scans. Use
paired CPS list/detail calls and compare material fields with OpenStack CLI;
this task creates no provider resource. Add evidence to
`cps/docs/runbooks/sprint-19-portal-parity.md`.

Proposed commit: `feat(inventory): expose Horizon-parity catalog semantics`
