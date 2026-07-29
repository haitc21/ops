# OPS-1801 — Cross-resource convergence and recovery

**Status:** In progress
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
