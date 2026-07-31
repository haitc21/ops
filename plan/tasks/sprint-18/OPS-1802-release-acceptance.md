# OPS-1802 — Migration, runbook, and release acceptance support

**Status:** Done
**Active backlog:** No
**Points:** 8
**Paired task:** CPS-1802

## Outcome

OPS artifacts required for the Sprint 18 release gate are pinned, tested, and
documented alongside CPS runbook steps.

## Tasks

1. Record OpenStack service versions and capability matrix for the lab provider.
2. Support dependency-ordered cleanup for floating IPs, attachments, instances,
   and network topology from OPS handlers.
3. Verify contract checksum parity with CPS for resource operation messages.
4. Participate in disposable real-cloud / lab acceptance scenario with zero
   residual provider resources.
5. Document nested-lab hypervisor caveats in OPS-1803 when hook-based workarounds
   remain required.

## Acceptance tests

- Full OPS unit/contract/integration suites pass.
- Lab scenario: FIP allocate + associate succeeds via CPS without CLI recovery.
- Cleanup ledger confirms no disposable-prefix resources remain on OpenStack.

## Verification

```bash
cd ops
uv run ruff check .
uv run mypy src
uv run pytest -q
```

## Out of scope

- CPS migration lifecycle (owned by CPS-1802).
- Production change windows and backup approval.
- Superseded console and TMS.

## 2026-07-31 verification

- CPS/OPS contract manifest parity is byte-identical at SHA-256
  `b46f6bf5fa5913f5561c4cf53a9f2adae3d0327a951f02d0ebe7f151328841bc`.
- OPS contract tests pass `83`; current Compose OPS API is healthy and
  `/health/ready` returns `status: ok`.
- RabbitMQ 4.3 messaging integration passes (`22 passed`); temporary test
  queues are exclusive and no longer use the disabled transient
  non-exclusive queue feature.
- Final default gate: Ruff and mypy pass; pytest reports `453 passed, 24
  skipped`.
- Previously recorded lab capability evidence reports Keystone `v3.14`, Nova
  microversion `2.100`, Glance `2.17`, Neutron available, and block storage
  available after the catalog repair.
- CPS inventory reports zero active `cmp180-*` resources across the named
  instance, network, subnet, router, volume, snapshot, and keypair projections.

- Provider-authoritative controller queries returned zero `cmp180-*` servers,
  networks, subnets, routers, volumes, snapshots, and keypairs.
- Nova reports `compute01` and `compute02` enabled/up. Cold migration to
  `compute02` and reverse migration to `compute01` both completed and were
  confirmed; TCP/22 remained reachable after each move.
