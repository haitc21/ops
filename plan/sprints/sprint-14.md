# Sprint 14 — VM create terminal convergence and replay safety

**Status:** Completed — implementation, regression gates, and live OpenStack acceptance verified  
**Dates:** 2026-10-03 to 2026-10-16  
**Capacity:** 26 OPS points  
**Sprint Goal:** OPS reports a durable terminal result after Nova reaches its
terminal state, without allowing optional enrichment, an unbounded SDK call,
result-publication failure, redelivery, or worker restart to strand the CPS
operation.

**Repository constraint:** OPS and the pinned CPS contract are the only delivery
scope. OPS must not call or modify TMS, LMS, or BMS.

## Selected stories

| Story | Points | Owner | CPS dependency | Status |
|---|---:|---|---|---|
| OPS-1301 Bounded Nova convergence and enrichment | 8 | OPS | CPS-1301 | Completed |
| OPS-1302 Replay-safe server discovery | 5 | OPS | None | Completed |
| OPS-1303 Reliable terminal result publication | 8 | OPS | CPS-1301 | Completed |
| OPS-1304 VM-create reconciliation handler | 5 | OPS | CPS-1302 | Completed |

## Task backlog

| Task | Deliverable | Depends on | Status |
|---|---|---|---|
| [OPS-1301](../tasks/sprint-14/OPS-1301-bounded-create-convergence.md) | Per-call deadlines and nonblocking relationship enrichment | OPS-406 | Completed |
| [OPS-1302](../tasks/sprint-14/OPS-1302-operation-marker-replay.md) | Find an existing server by immutable operation metadata | OPS-401, OPS-501 | Completed |
| [OPS-1303](../tasks/sprint-14/OPS-1303-terminal-result-publication.md) | Independently retryable, confirmed terminal publication | OPS-102, OPS-1301 | Completed |
| [OPS-1304](../tasks/sprint-14/OPS-1304-instance-create-reconciliation.md) | Read-only provider reconciliation for stale create operations | OPS-1302, OPS-1303 | Completed |

## Execution sequence

1. Reproduce and instrument the current `ACTIVE` server / 20-percent operation.
2. Complete OPS-1302 first to prevent duplicate Nova mutation during every
   subsequent retry test.
3. Complete OPS-1301 with per-call and whole-handler deadlines.
4. Complete OPS-1303 and prove a terminal result can recover after partial
   publication.
5. Complete OPS-1304 and integrate with CPS-1302.
6. Run unit, contract, mocked service-hang, RabbitMQ restart, worker restart,
   and real OpenStack acceptance suites.

## Acceptance

- `get_server` and every Neutron/Cinder enrichment request have a bounded
  deadline; a blocked SDK call cannot defeat the whole-handler deadline.
- Nova `ACTIVE` or `SHUTOFF` determines successful create convergence.
  Optional port/volume enrichment cannot keep the operation running.
- A requested floating IP remains a required postcondition and returns a
  normalized terminal failure when allocation or association cannot complete.
- Redelivery finds the existing server by metadata `cmp_operation_id`; it does
  not rely on a synthetic server name and does not create a duplicate VM.
- Progress includes the Nova server ID before optional enrichment.
- If progress publishes but completed publication fails, retry republishes the
  same terminal outcome without repeating `create_server`.
- Worker restart after Nova creation converges through the reconciliation
  handler.
- Stage latency and outcome logs/metrics include operation/server/provider IDs
  and contain no credentials, tokens, user data, or raw SDK response bodies.

## Risks and impediments

| Risk/impediment | Owner | Mitigation | Status |
|---|---|---|---|
| Cancelling `asyncio.to_thread` does not stop the underlying HTTP call | OPS | Configure SDK/session request timeout in addition to coroutine deadlines | Open |
| Metadata filtering differs across Nova deployments | OPS | Use server ID first; otherwise bounded server listing plus exact metadata comparison | Open |
| Optional enrichment is incomplete at completed time | CPS/OPS | Return explicit warnings and schedule targeted inventory refresh | Open |
| Partial multi-message publication repeats progress | OPS/CPS | Deterministic message IDs and inbox deduplication; terminal publication recoverable independently | Open |

## Review evidence

- SDK-call and whole-handler timeout tests:
- Cinder/Neutron hang tests:
- Replay without duplicate Nova server:
- Partial publish and RabbitMQ restart:
- Worker restart and reconcile:
- Real OpenStack acceptance: VM `11b37956-de5c-4017-b890-a862232e2b2e` reached ACTIVE and SSH from the host returned `SSH_OK`.

## Retrospective actions

- Keep:
- Improve:
- One measurable action for the next sprint:
