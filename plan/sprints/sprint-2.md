# Sprint 2 — OpenStack provider validation vertical slice

**Status:** Complete — real OpenStack acceptance verified
**Canonical CPS plan:** `../cps/docs/superpowers/plans/2026-07-22-sprint-2-provider-validation.md`
**OPS working reference:** `docs/superpowers/plans/2026-07-22-sprint-2-provider-validation.md`

## Scope and story backlog

| Story | Points | Status | Acceptance gate |
|---|---:|---|---|
| OPS-201 CPS credential resolver client | 5 | Complete | bounded CPS call, retry classification, no cache/log/persistence |
| OPS-202 OpenStackSDK connection factory | 8 | Complete | exact project/domain/region/TLS/interface/CA/timeouts/user-agent |
| OPS-203 Service discovery and capability mapper | 8 | Complete | Keystone/Nova/Neutron/Glance/Cinder normalized, optional-safe |
| OPS-204 Connection validation handler | 5 | Complete | safe reads, progress/terminal confirms, replay/DLQ/no secret |

**OPS total:** 26 points. CPS owns canonical contracts, credentials, operations, and persistence.
Inventory, VM lifecycle, additional auth methods, and Sprint 3 are deferred.

## Checkpoints

| Checkpoint | Deliverable | Status |
|---|---|---|
| CP0 | Ubuntu readiness and portability gate | Done — OPS `89173ba` |
| CP1 | CPS-owned validation contracts pinned into OPS | Done — 12 artifacts pinned; validators pass |
| CP7 | OPS-201 resolver | Done |
| CP8 | OPS-202 scoped SDK factory | Done |
| CP9 | OPS-203 discovery and multi-event confirm transport | Done |
| CP10 | OPS-204 real validation handler | Done |
| CP12 | Synthetic and real OpenStack acceptance | Done — real controller validation verified |
| CP13 | Full verification and closure | Done |

## CP0 evidence

Ubuntu 26.04 LTS and CPython 3.12.13 are active. OPS has no database runtime. The existing
RabbitMQ 4.1 runtime, retry/DLQ, publisher-confirm, and manual-ACK policies remain the delivery
boundary. The OpenStack VMs answer Keystone `/v3` from the VM network; host-side route/name
resolution must be verified before real acceptance. Runtime connection values remain ignored local
configuration and never enter OPS `.env` as plaintext credentials.

## Definition of Done

All four OPS stories have pushed commits; OPS pins CPS contracts byte-for-byte; resolver/factory/
discovery/handler tests, RabbitMQ integration, OpenStackSDK deprecation-as-error tests, full gates,
Docker build, pre-commit, and secret checks pass; real product-path validation succeeds without
provider mutation or secret leakage; both repositories are synchronized. Do not mark Done without
the CPS inbox/capability evidence and do not start inventory or VM work.

## Implementation and evidence

Follow the canonical plan in the CPS repository. Cursor Composer 2.5 Fast is the worker; Codex owns
architecture, review, verification, commit, and push. Record task SHAs and non-secret validation
evidence here after CP13 only.

### CP1 contract pin evidence — 2026-07-22

- CPS canonical and OPS pin contain identical validation schemas and updated event fixtures.
- OPS contract suite: 81 passed; semantic validation and checksum validation pass.
- No runtime credential resolver, SDK factory, or provider mutation has been added.

### Sprint 2 implementation evidence — 2026-07-22

- Resolver uses bounded httpx connect/read/write/pool timeouts, a 16 KiB response cap, normalized
  CPS errors, and no cache or persistence. The SDK factory uses `CPS-OPS/0.1`, no cloud release or
  fixed microversion, and cleans owner-only CA temp files in `finally`.
- Discovery always emits the five service and six feature keys; identity/compute are required and
  optional service absence is represented safely. The real handler emits deterministic RUNNING,
  WAITING_PROVIDER, and terminal events; the consumer confirms each event before one ACK.
- OPS full suite: 323 passed, 24 skipped; Ruff and mypy pass. Contract tree and CPS pin both pass
  with 12 artifacts. Factory construction and synthetic discovery smoke checks pass.
- Real OpenStack acceptance passed through `controller` at `192.168.122.253`; OPS resolved the CPS
  credential, authenticated against Keystone, discovered the service catalog, and emitted the
  ordered progress and terminal events consumed by CPS. No credentials were committed.
