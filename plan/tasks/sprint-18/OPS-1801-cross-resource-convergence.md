# OPS-1801 — Cross-resource convergence and recovery

**Status:** Done
**Active backlog:** No — handler convergence, deterministic errors, and
messaging recovery are covered by the automated suites.
**Points:** 13
**Paired task:** CPS-1801

## Outcome

OPS handlers converge provider mutations under duplicate delivery, partial
failure, and late terminal results without unsafe inferred deletion.

## Tasks

1. Pin network resource error normalization to the correct `provider_service`.
2. Validate floating IP associate requires `port_id` before Neutron mutation.
3. Preserve replay-safe identity, volume, and network operation semantics under
   duplicate command delivery.
4. Emit deterministic terminal states for provider 401/403/404/409/429/5xx.
5. Document unsupported operations and capability skips in sprint evidence.

## Acceptance tests

- Floating IP associate without `port_id` fails fast with validation error.
- Network operation failures report `provider_service: network`, not `identity`.
- Duplicate floating IP associate idempotency replays the same operation record.
- Unit and contract suites pass in CI.

## Verification

```bash
cd ops
uv run ruff check .
uv run mypy src
uv run pytest tests/unit/application/test_network_operations.py \
  tests/unit/application/test_provider_service_mapping.py -q
```

## Out of scope

- CPS durable operation persistence (owned by CPS-1801).
- Console access and TMS authorization.
- Physical-infrastructure acceptance.

## Verification evidence

- Network validation and provider-service mapping:
  `tests/unit/application/test_network_operations.py` and
  `test_provider_service_mapping.py`.
- Replay-safe volume attachment, snapshot, and keypair mutations:
  `test_volume_operations.py`, `test_snapshot_operations.py`, and
  `test_keypair_operations.py`.
- Resize/rebuild recovery without reissuing an already completed provider
  mutation: `test_instance_action.py`.
- Retry exhaustion, DLQ routing, graceful shutdown, redelivery, and reconnect:
  `tests/integration/messaging/test_ack_policy.py` and
  `test_graceful_shutdown.py`.
- Real-cloud resource results and cleanup are recorded by the paired CPS task
  and its linked Sprint 3, 16, 17, and volume/snapshot runbook evidence.

No OPS-1801-specific live gate remains. Migration rehearsal and release-tag
checksum are owned by OPS-1802; console is superseded.
