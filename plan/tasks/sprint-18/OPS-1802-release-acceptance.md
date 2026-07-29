# OPS-1802 — Migration, runbook, and release acceptance support

**Status:** In progress
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
