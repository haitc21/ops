# OPS-1906 Horizon semantic parity micro-plan

- Story: OPS-1906, paired with CPS-1906; CPS remains canonical.
- Outcome: supported OpenStackSDK inventory mapping supplies bounded image/flavor
  status, visibility, public/protection, metadata, tags, and access IDs.
- Exact files: `src/ops/openstack/inventory.py`,
  `tests/unit/openstack/test_inventory.py`, pinned contract files only if a
  canonical wire artifact changes, and the paired CPS runbook.
- Out of scope: Django/Horizon runtime, novaclient/glanceclient, image bytes,
  credentials, raw provider bodies, unbounded N+1 calls, and mutations.
- Compatibility: additive fields only; image visibility remains canonical while
  `is_public` is persisted as a safe derived field for consumer parity.

## RED-GREEN-REFACTOR and gates

- [x] RED mapper test observed failing for image public normalization.
- [x] GREEN minimal mapper implementation; status normalization is limited to
  image/flavor catalog resources and access enrichment remains bounded.
- [x] Focused/full tests, mypy, ruff, contract validation, and diff check pass.
- [ ] Independent reviewer approval and remediation re-review.
- [ ] Live CPS list/detail versus `openstack image/flavor list/show`; no provider
  mutation or cleanup resource is expected.
- [ ] Secret scan and redacted runbook closure.
- Proposed commit: `feat(inventory): expose Horizon-parity catalog semantics`.
