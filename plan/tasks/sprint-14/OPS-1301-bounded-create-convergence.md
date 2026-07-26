# OPS-1301 — Bounded VM-create convergence

**Status:** Completed — verified by OPS tests and live OpenStack acceptance.

## Goal

Return a terminal VM-create result after Nova reaches a terminal state without
allowing an SDK request or optional relationship enrichment to block forever.

## Design

- Add a per-request timeout to the OpenStack SDK session.
- Add an async deadline around each blocking SDK call and a whole-handler
  deadline shorter than the CPS operation deadline.
- Update the common waiter so a blocked `fetch()` cannot bypass its deadline.
- Treat Nova `ACTIVE` and `SHUTOFF` as create success and `ERROR` as terminal
  provider failure.
- Run port and volume collection as bounded best-effort enrichment after Nova
  convergence.
- Avoid scanning every Cinder volume when server attachment data or a filtered
  query is available.
- Keep requested floating-IP allocation/association as a required postcondition
  with its own deadline and normalized failure.
- Record structured stage duration and outcome fields.

## Acceptance

- A never-returning `get_server` call ends with a normalized timeout.
- An `ACTIVE` server plus unavailable or hanging Cinder completes successfully
  with an explicit enrichment warning.
- An `ACTIVE` server plus unavailable Neutron completes when no floating IP is
  required.
- Required floating-IP failure produces a terminal failed result.
- No handler remains active beyond the configured whole-handler deadline.
- Waiter, handler, and mocked service-hang tests are deterministic.
