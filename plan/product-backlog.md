# OPS Product Backlog

Current public model note: OPS consumes one `provider` aggregate from CPS.
`credential` and `provider connection` are internal implementation details
from earlier iterations and are not separate public business objects in new
work.

Restart baseline note: CPS and OPS operate independently. They do not integrate
with TMS or BMS. A provider represents an entire OpenStack cluster; CPS supplies
OPS with the encrypted highest-privilege admin credential and cluster connection
metadata through the provider aggregate. Organization and workspace bindings are
deferred future scope only. From Sprint 0 onward, planning and delivery
prioritize standalone provider onboarding and provider-level validation.

## Epic OPS-E0 — Engineering foundation

### OPS-001 — Bootstrap a reproducible Python service

- **Sprint/Priority/Points:** 0 / Must / 5
- **Outcome:** stateless service builds and runs consistently on Python 3.12.
- **Tasks:** `pyproject.toml`, src layout, lockfile, FastAPI health app, worker entrypoint, Python 3.12 runtime Dockerfile, lint/type/test tooling.
- **Acceptance:** clean locked install; worker and health API start; quality commands pass; no business database dependencies exist.

### OPS-002 — Typed configuration and secret-safe observability

- **Sprint/Priority/Points:** 0 / Must / 5
- **Depends on:** OPS-001
- **Acceptance:** RabbitMQ/CPS/OpenStack timeout/TLS settings validate; structured logs redact credentials, tokens, auth headers, CA secrets, and user data; correlation/operation/message IDs propagate.

### OPS-003 — Health/readiness lifecycle

- **Sprint/Priority/Points:** 0 / Must / 3
- **Depends on:** OPS-001
- **Acceptance:** liveness is process-only; readiness reflects RabbitMQ consumer connectivity; customer OpenStack outage does not make whole OPS unready; graceful shutdown stops intake and finishes/nacks safely.

### OPS-004 — Local quality pipeline

- **Sprint/Priority/Points:** 0 / Must / 5
- **Depends on:** OPS-001..003
- **Acceptance:** Husky pre-commit runs format, lint, typing, default tests, contract validation, and secret scan; infrastructure-backed gates are reserved for the GitLab pipeline.

## Epic OPS-E1 — Contracts and messaging

### OPS-101 — Pin and validate CPS contracts

- **Sprint/Priority/Points:** 1 / Must / 5
- **Depends on:** CPS-101
- **Acceptance:** copied schemas/checksum match CPS; all golden fixtures validate; unsupported major versions reject; SDK objects cannot be serialized into events.

### OPS-102 — RabbitMQ topology and robust runtime

- **Sprint/Priority/Points:** 1 / Must / 8
- **Depends on:** OPS-001, OPS-101
- **Tasks:** robust connection, durable topology, bindings, publisher confirms, manual ack, prefetch, reconnect, graceful shutdown, DLX behavior.
- **Acceptance:** declarations are idempotent; reconnect restores consumer; result confirm precedes command ack; failed handler nacks according to classification; poison messages reach DLQ.

### OPS-103 — Error normalization and retry policy

- **Sprint/Priority/Points:** 1 / Must / 8
- **Depends on:** OPS-101
- **Acceptance:** SDK auth/forbidden/not-found/conflict/quota/rate-limit/timeout/network/5xx map to stable common errors; retryable flag is correct; backoff uses jitter/Retry-After; raw unsafe body is excluded; provider request ID retained.

### OPS-104 — Handler dispatch and envelope validation

- **Sprint/Priority/Points:** 1 / Must / 5
- **Depends on:** OPS-101..103
- **Acceptance:** message type routes to one handler; malformed/unsupported envelope fails deterministically; progress/result use causation/correlation/operation IDs; no business mutation occurs before validation.

## Epic OPS-E2 — Connection and capability vertical slice

Legacy implementation note: this epic reflects the earlier OPS split around
credential resolution and provider connection scope. The current public model
uses a single provider aggregate; treat these stories as historical/internal
unless a future migration explicitly reopens them.

### OPS-201 — CPS credential resolver client

- **Sprint/Priority/Points:** 2 / Must / 5
- **Depends on:** OPS-002, OPS-104, CPS-204
- **Acceptance:** resolves credential by reference with bounded timeout; never persists/caches/logs secret; CPS unavailable maps retryably; response is zeroed/released after handler scope as practicable.

### OPS-202 — OpenStackSDK connection factory

- **Sprint/Priority/Points:** 2 / Must / 8
- **Depends on:** OPS-201
- **Acceptance:** creates username/password project/domain/region-scoped connection; TLS/interface/CA options apply; session timeout and user agent set; no fixed OpenStack release/microversion assumption.

### OPS-203 — Service discovery and capability mapper

- **Sprint/Priority/Points:** 2 / Must / 8
- **Depends on:** OPS-202
- **Acceptance:** discovers Keystone/Nova/Neutron/Glance/Cinder endpoints and versions; reports supported/unsupported reasons; absent optional service does not crash validation; capability payload validates CPS schema.

### OPS-204 — Connection validation handler

- **Sprint/Priority/Points:** 2 / Must / 5
- **Depends on:** OPS-102..103, OPS-203
- **Coordinates with:** CPS-205
- **Acceptance:** real OpenStack validation publishes progress and terminal result; credentials absent from message/event/log; replay repeats safe reads only; auth and service failures normalize correctly.

### OPS-205 — Single-endpoint provider onboarding support

- **Sprint/Priority/Points:** 6 / Must / 8
- **Depends on:** OPS-201..204
- **Coordinates with:** CPS-207
- **Acceptance:** OPS treats provider onboarding as one privileged provider
  request from CPS; no user-selected scope is required; the handler validates
  and discovers capabilities from the supplied highest-privilege account; the
  provider aggregate already carries the encrypted secret and internal
  connection metadata, so no separate credential/connection object is needed;
  public/provider-side objects do not escape the handler boundary; replay-safe
  validation and discovery still work.

## Epic OPS-E3 — Inventory

### OPS-301 — Inventory collection coordinator

- **Sprint/Priority/Points:** 3 / Must / 8
- **Depends on:** OPS-203
- **Acceptance:** coordinates supported collections with per-service timeout/pagination; explicitly reports unsupported/failed/complete; cancellation/shutdown nacks safely; never reports success for incomplete required collection.

### OPS-302 — Identity, compute, and image collectors/mappers

- **Sprint/Priority/Points:** 3 / Must / 13
- **Depends on:** OPS-301, OPS-101
- **Scope:** region, project, flavor, image, instance.
- **Acceptance:** every SDK resource maps to typed common fixture; provider IDs/timestamps/status/attributes are safe; pagination and missing optional fields work; no SDK object crosses contract boundary.

### OPS-303 — Network and storage collectors/mappers

- **Sprint/Priority/Points:** 3 / Must / 13
- **Depends on:** OPS-301, OPS-101
- **Scope:** network, subnet, port, volume and attachments.
- **Acceptance:** relationships retain provider IDs; pagination works; service absence reports unsupported; provider attributes are minimal/versioned; mapper golden tests pass.

### OPS-304 — Inventory batch publisher

- **Sprint/Priority/Points:** 3 / Must / 8
- **Depends on:** OPS-102, OPS-302..303
- **Coordinates with:** CPS-302..303
- **Acceptance:** configurable size limits; per-type sequence starts at one; deterministic checksum; `is_last` closes collection; publish confirms; final success only after all batch confirms; replay emits equivalent identities/checksums.

### OPS-305 — Targeted refresh and tombstones

- **Sprint/Priority/Points:** 3 / Must / 8
- **Depends on:** OPS-302..304
- **Coordinates with:** CPS-305
- **Acceptance:** supported resource fetched by provider ID; related VM ports/volumes refresh where required; NotFound emits tombstone; timeout/401/403/unavailable never emits deletion.

## Epic OPS-E4 — VM lifecycle

### OPS-401 — Create VM handler

- **Sprint/Priority/Points:** 4 / Must / 13
- **Depends on:** OPS-103, OPS-202, OPS-305, CPS-401
- **Acceptance:** supports IMAGE and VOLUME_FROM_IMAGE; explicit networks/ports, SGs, key pair, AZ, metadata, config drive and bounded user data map correctly; block-device delete policy matches request; capability/scope validated before mutation; operation marker supports replay detection.

### OPS-402 — Instance detail operation

- **Sprint/Priority/Points:** 4 / Must / 5
- **Depends on:** OPS-302
- **Acceptance:** returns normalized instance plus relevant port/volume snapshots; NotFound returns tombstone; no provider object escapes.

### OPS-403 — Start and stop handlers

- **Sprint/Priority/Points:** 4 / Must / 8
- **Depends on:** OPS-202, OPS-305
- **Acceptance:** state preconditions prevent invalid duplicate mutation; waiter reaches target or normalized error/timeout; replay observes state and republishes result without repeating action unnecessarily.

### OPS-404 — Reboot handler

- **Sprint/Priority/Points:** 4 / Must / 5
- **Depends on:** OPS-403
- **Acceptance:** defined reboot type maps correctly; waiter handles transient state; invalid state and timeout normalize; final instance snapshot publishes.

### OPS-405 — Delete handler and root-volume semantics

- **Sprint/Priority/Points:** 4 / Must / 8
- **Depends on:** OPS-401, OPS-305
- **Acceptance:** delegates root deletion to Nova `delete_on_termination`; waits for absence; existing absence is idempotent success/tombstone; related ports/volumes refresh; no blind second volume delete.

### OPS-406 — Common waiter layer

- **Sprint/Priority/Points:** 4 / Must / 8
- **Depends on:** OPS-103, OPS-202
- **Acceptance:** configurable interval/deadline; ACTIVE/SHUTOFF/ERROR/deleted terminal handling; provider fault safely captured; wait polling distinguished from command retry; tests use deterministic clock/sleeper.

## Epic OPS-E5 — Recovery and release readiness

### OPS-501 — Stateless replay safety

- **Sprint/Priority/Points:** 5 / Must / 13
- **Depends on:** OPS-401..406
- **Acceptance:** duplicate create finds operation marker before mutation; power/delete replays inspect provider state; result publish failure followed by redelivery republishes same outcome; process restart loses no durable truth because CPS/provider remain authoritative.

### OPS-502 — Concurrency, backpressure, and graceful shutdown

- **Sprint/Priority/Points:** 5 / Must / 8
- **Depends on:** OPS-102, OPS-301
- **Acceptance:** bounded handler/service concurrency and prefetch; no unbounded task creation; shutdown stops intake, finishes or nacks work, closes SDK/Rabbit resources; load test meets agreed stability threshold.

### OPS-503 — Provider observability

- **Sprint/Priority/Points:** 5 / Should / 5
- **Depends on:** OPS-002, OPS-103
- **Acceptance:** metrics cover queue lag/redelivery/DLQ, provider latency/error, collection/batch count, waiter results; traces/logs carry IDs and provider request ID; secret scan/redaction tests pass.

### OPS-504 — Mocked integration suite

- **Sprint/Priority/Points:** 5 / Must / 8
- **Depends on:** all Must handler stories
- **Acceptance:** RabbitMQ + fake CPS credential API + mocked OpenStack HTTP exercise validation, inventory, lifecycle, retries, redelivery, timeout, and contract errors without real secrets.

### OPS-505 — Real OpenStack acceptance and compatibility report

- **Sprint/Priority/Points:** 5 / Must / 13
- **Depends on:** OPS-501..504 and paired CPS stories
- **Acceptance:** approved eight-scenario suite passes; discovered service/API versions and capabilities recorded; both boot modes work; direct drift converges; restart/redelivery produces no duplicate VM or lost terminal result.

## Deferred backlog

- Organization/workspace binding command handlers (`OPS-704`; canonical future
  design:
  `../../../cps/docs/superpowers/specs/2026-07-24-openstack-cmp-org-workspace-binding-spec.md`).
- OpenStack notification/event bus integration.
- Existing-volume boot mode.
- Resize, rebuild, rescue, shelve, migration, and console operations.
- Shared contract package after multiple provider services justify it.

## Epic OPS-E7 — Scope and identity inventory

### OPS-701 — Pin scope/resource-operation contracts

- **Sprint/Priority/Points:** 7 / Must / 5
- **Depends on:** CPS-701
- **Acceptance:** pinned artifacts/checksum match CPS; malformed scope/owner
  combinations reject before provider access; existing fixtures remain valid.

### OPS-702 — Effective provider privilege scope discovery

- **Sprint/Priority/Points:** 7 / Must / 8
- **Depends on:** OPS-202, CPS-702
- **Acceptance:** supported SDK behavior reports effective system/domain/project
  privilege scope and operation capabilities from the provider aggregate
  without exposing token/catalog or inferring authority from username.

### OPS-703 — Domain/project collectors and mappers

- **Sprint/Priority/Points:** 7 / Must / 8
- **Depends on:** OPS-301..305, CPS-703
- **Acceptance:** paginated collectors emit safe typed identities; forbidden
  domain collection is explicit; project collection remains project-scope
  compatible; targeted NotFound alone emits tombstone.

## Epic OPS-E8 — Identity lifecycle and quotas

### OPS-801 — Domain/project lifecycle handlers

- **Sprint/Priority/Points:** 8 / Must / 13
- **Depends on:** OPS-701..703, CPS-801
- **Acceptance:** create/update/disable/delete use scope/capability checks,
  provider preconditions, bounded waiters, replay detection, normalized result,
  conflict, and already-absent behavior.

### OPS-802 — Role assignment collectors/handlers

- **Sprint/Priority/Points:** 8 / Must / 8
- **Depends on:** OPS-703, CPS-802
- **Acceptance:** role inventory and assignment ensure/revoke are scope-aware,
  idempotent, and secret-free.

### OPS-803 — Quota collectors/handlers

- **Sprint/Priority/Points:** 8 / Must / 8
- **Depends on:** OPS-203, CPS-803
- **Acceptance:** compute/network/block-storage quota behavior normalizes
  unlimited values and partial service failure; replay checks current values.

### OPS-804 — Identity real-cloud acceptance

- **Sprint/Priority/Points:** 8 / Must / 8
- **Depends on:** OPS-801..803 and CPS-804
- **Acceptance:** disposable identity lifecycle, replay/restart, and cleanup
  pass with capability/version evidence.

## Epic OPS-E9 — Network control

### OPS-901 — Network inventory expansion

- **Sprint/Priority/Points:** 9 / Must / 13
- **Depends on:** OPS-303..305, CPS-901
- **Acceptance:** routers/interfaces/security groups/rules/floating IPs and
  ownership relationships map and reconcile without SDK leakage.

### OPS-902 — Network/subnet handlers

- **Sprint/Priority/Points:** 9 / Must / 8
- **Depends on:** OPS-701, OPS-901, CPS-902
- **Acceptance:** CRUD uses supported SDK proxies, strict provider ownership,
  replay preconditions, dependency conflicts, and normalized snapshots.

### OPS-903 — Router/interface handlers

- **Sprint/Priority/Points:** 9 / Must / 8
- **Depends on:** OPS-902, CPS-903
- **Acceptance:** router CRUD and interface ensure/remove recover from duplicate
  and partial relationship delivery.

### OPS-904 — Port/security handlers

- **Sprint/Priority/Points:** 9 / Must / 13
- **Depends on:** OPS-901..902, CPS-904
- **Acceptance:** port/security-group/rule lifecycle validates project,
  network, fixed IP, protocol, and ranges before mutation.

### OPS-905 — Floating-IP handlers

- **Sprint/Priority/Points:** 9 / Must / 8
- **Depends on:** OPS-902..904, CPS-905
- **Acceptance:** allocate/associate/disassociate/release is relationship-aware,
  replay-safe, and returns affected port/network snapshots.

### OPS-906 — Network topology acceptance

- **Sprint/Priority/Points:** 9 / Must / 8
- **Depends on:** OPS-901..905 and CPS-906
- **Acceptance:** disposable topology creation/removal, restart, drift, and
  cleanup pass without direct provider mutation outside the test harness.

## Epic OPS-E10 — Storage, image, and compute catalog

### OPS-1001 — Volume-type/snapshot collectors

- **Sprint/Priority/Points:** 10 / Must / 13
- **Depends on:** OPS-301..305, CPS-1001
- **Acceptance:** typed volume types/snapshots paginate, map, refresh, and
  tombstone safely.

### OPS-1002 — Volume lifecycle/attachment handlers

- **Sprint/Priority/Points:** 10 / Must / 13
- **Depends on:** OPS-1001, OPS-406, CPS-1002
- **Acceptance:** create/update/extend/attach/detach/delete is state-aware,
  bounded, replay-safe, multiattach-capability aware, and never double-deletes
  Nova-owned root volumes.

### OPS-1003 — Snapshot lifecycle handlers

- **Sprint/Priority/Points:** 10 / Must / 8
- **Depends on:** OPS-1001..1002, CPS-1003
- **Acceptance:** snapshot create/update/delete has waiters, dependency checks,
  replay behavior, and normalized tombstones.

### OPS-1004 — Image metadata/import/access handlers

- **Sprint/Priority/Points:** 10 / Must / 13
- **Depends on:** OPS-701, CPS-1004
- **Acceptance:** supported Glance operations are replay-safe; bytes/signed
  credentials never traverse RabbitMQ; unsupported upload reports capability.

### OPS-1005 — Availability-zone/flavor collectors and handlers

- **Sprint/Priority/Points:** 10 / Should / 13
- **Depends on:** OPS-301, CPS-1005
- **Acceptance:** AZ, flavor specs/access map safely; admin mutations are
  capability/scope gated and use supported compute APIs.

### OPS-1006 — Storage/catalog acceptance

- **Sprint/Priority/Points:** 10 / Must / 8
- **Depends on:** OPS-1001..1005 and CPS-1006
- **Acceptance:** storage/catalog workflows pass compatibility, replay,
  convergence, and cleanup gates.

## Epic OPS-E11 — Expanded control-plane release

### OPS-1101 — Cross-resource replay and drift suite

- **Sprint/Priority/Points:** 11 / Must / 13
- **Depends on:** OPS-E8..E10
- **Acceptance:** provider state checks converge identity, network, storage, and
  catalog operations after duplicate, restart, direct drift, and partial
  success.

### OPS-1102 — Compatibility and operational controls

- **Sprint/Priority/Points:** 11 / Must / 8
- **Depends on:** OPS-1101
- **Acceptance:** service/API versions, extensions, scope behavior, metrics,
  DLQ replay, and cleanup procedures are documented and tested.

### OPS-1103 — Real-cloud release acceptance

- **Sprint/Priority/Points:** 11 / Must / 13
- **Depends on:** OPS-1101..1102 and CPS-1104
- **Acceptance:** approved provider matrix passes with no leaked secret,
  duplicate mutation, orphaned disposable resource, or lost terminal result.

## Epic OPS-E12 — Provider tenancy contract

### OPS-1201 — Provider-owned credential and project-owner contract

- **Sprint/Priority/Points:** 13 / Must / 8
- **Depends on:** CPS-1201, CPS-1202
- **Acceptance:** commands resolve access by provider connection without a
  credential reference; every tenant resource mapper preserves provider project
  identity; malformed legacy commands fail before OpenStack access; secrets and
  SDK objects remain adapter-local.

### OPS-1202 — Authorization decision command context

- **Sprint/Priority/Points:** 13 / Must / 5
- **Depends on:** CPS-1203, OPS-1201
- **Acceptance:** user commands require safe CPS decision metadata; missing,
  malformed, and expired contexts fail before mutation; OPS receives no bearer
  token, performs no TMS/LMS call, and makes no tenant-role decision.
