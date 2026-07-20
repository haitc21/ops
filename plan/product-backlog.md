# OPS Product Backlog

## Epic OPS-E0 — Engineering foundation

### OPS-001 — Bootstrap a reproducible Python service

- **Sprint/Priority/Points:** 0 / Must / 5
- **Outcome:** stateless service builds and runs consistently on Python 3.12.
- **Tasks:** `pyproject.toml`, src layout, lockfile, FastAPI health app, worker entrypoint, Python 3.12 runtime Dockerfile, lint/type/test tooling.
- **Acceptance:** clean locked install; worker and health API start; quality commands pass; no DB/Valkey dependencies exist.

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

- OpenStack notification/event bus integration.
- Existing-volume boot mode.
- Floating IP, snapshot, resize, rebuild, rescue, shelve, migration, and console operations.
- Provider-side security group/key pair/network/volume CRUD.
- Shared contract package after multiple provider services justify it.
