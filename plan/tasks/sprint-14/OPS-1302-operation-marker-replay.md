# OPS-1302 — Replay-safe server discovery by operation marker

## Goal

Find and reuse the Nova server created by an earlier delivery so retries and
worker restarts never create a duplicate VM.

## Design

- Treat metadata `cmp_operation_id` as the immutable create idempotency marker.
- Prefer a persisted provider server ID supplied by CPS.
- If no server ID exists, perform a bounded lookup and compare the metadata
  value exactly.
- Do not call `find_server("cmp-operation-...")`; the user-visible server name
  is independent of the operation marker.
- Handle the case where Nova commits create but the SDK loses the response.
- Reject ambiguous multiple matches as a provider conflict and expose safe
  diagnostics.

## Acceptance

- Redelivery after successful Nova create reuses the same server.
- Restart between `create_server` and terminal result creates no second server.
- A lost create response reconciles to the committed server.
- Same display names from unrelated operations never match.
- Ambiguous operation markers fail safely without provider mutation.
