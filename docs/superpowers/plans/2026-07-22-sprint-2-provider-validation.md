# Sprint 2 Provider Validation Vertical Slice — OPS Working Reference

The canonical executable plan is maintained in the CPS repository at
`cps/docs/superpowers/plans/2026-07-22-sprint-2-provider-validation.md`. This OPS working reference
uses the same task IDs, interfaces, dependencies, RED/GREEN commands, review gates, and closure
criteria. CPS owns the canonical contract/specification files; OPS copies and pins them byte-for-byte
from CPS before implementing OPS-201 through OPS-204.

The OPS-specific execution sequence is:

1. CP1 contract pin and semantic validators.
2. CP7 `application/credential_resolver.py` with bounded httpx and no secret retention.
3. CP8 `openstack/connection_factory.py` with exact scoped SDK options and CA cleanup.
4. CP9 `openstack/capabilities.py` plus ordered multi-event publisher-confirm transport.
5. CP10 real validation handler, registry wiring, retry/DLQ/replay tests.
6. CP12 fake-CPS/fake-OpenStack integration, then real OpenStack safe-read acceptance.
7. CP13 full OPS gates, CPS parity, evidence, and closure.

No OPS task may add a database, cache, credential store, raw SDK object contract, provider mutation,
or inventory/VM lifecycle behavior. Cursor is the implementer and must not perform Git operations;
Codex reviews and commits only after all gates pass. The full canonical task details and exact file
boundaries are the CPS plan linked above.
