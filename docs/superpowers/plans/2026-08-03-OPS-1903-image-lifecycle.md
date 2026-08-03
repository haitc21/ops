# OPS-1903 image lifecycle

Paired canonical CPS plan: `../../cps/docs/superpowers/plans/2026-08-03-CPS-1903-image-lifecycle.md`.
OPS validates the same pinned command before resolving credentials, then calls
only OpenStackSDK `connection.image` APIs.  URL imports are passed as validated
provider-side references only; no byte or credential material is fetched,
stored, logged, or returned.  Replay is based on operation marker metadata and
already-converged image state.

- [ ] RED SDK fake tests for import, metadata delta, members, state and delete.
- [ ] GREEN handler/dispatch with bounded, safe payloads and capability checks.
- [ ] Verify replay/unsupported/protected/invalid URL paths and full gates.
