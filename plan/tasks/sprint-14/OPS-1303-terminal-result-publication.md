# OPS-1303 — Reliable terminal result publication

## Goal

Make progress and terminal outcomes independently recoverable so a partial
multi-message publish cannot leave CPS permanently nonterminal.

## Design

- Publish progress with deterministic identity and the Nova server ID
  immediately after create/replay discovery.
- Give completed and failed events deterministic identities derived from the
  operation and outcome.
- Confirm each publish and retain enough provider truth to rebuild the terminal
  event on redelivery.
- On redelivery, inspect provider state before deciding whether any mutation is
  needed.
- Ensure retry after progress success and completed failure republishes the
  terminal event without repeating create.
- Bound retry attempts and route poison/unrecoverable messages according to the
  existing DLQ policy.

## Acceptance

- Progress-confirm success followed by completed-confirm failure is recovered.
- Duplicate progress and terminal events are byte-semantically equivalent and
  accepted idempotently by CPS.
- Command acknowledgement occurs only after the required terminal publication
  is confirmed.
- Broker reconnect and worker restart lose no terminal provider outcome.
- Tests cover failure at every publish/ack boundary.
