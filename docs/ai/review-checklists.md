# OPS AI Review Checklists

## Story kickoff

- [ ] Active sprint/story and paired CPS story identified.
- [ ] Canonical schema/version/checksum available.
- [ ] CodeGraph queried before code search when indexed.
- [ ] Relevant OpenStackSDK implementation/tests inspected.
- [ ] Capability, terminal state, timeout, retry, and replay strategy defined.

## Contract and mapper

- [ ] Pinned schema matches CPS.
- [ ] Golden valid/invalid fixtures pass.
- [ ] Unknown major/additive field behavior tested.
- [ ] SDK objects and exceptions do not cross boundary.
- [ ] Provider attributes are minimal, versioned, and secret-free.

## Provider operation

- [ ] Project/region/resource scope validated.
- [ ] Supported public SDK API used.
- [ ] Capability/version discovered, not assumed.
- [ ] Pagination/optional fields handled.
- [ ] Waiter has deterministic deadline and terminal/error states.
- [ ] Provider request ID retained safely.
- [ ] Root volume/network/user-data semantics match design.

## Reliability

- [ ] Envelope validated before mutation.
- [ ] Duplicate/redelivery behavior tested after provider success.
- [ ] Transient/permanent retry classification tested.
- [ ] Publisher confirm occurs before command ack.
- [ ] Concurrency/prefetch/deadline bounded.
- [ ] Shutdown safely finishes or nacks work.
- [ ] Poison messages reach DLQ.

## Security

- [ ] No real `clouds.yaml`, `.env`, token, password, key, or user data committed.
- [ ] Logs/errors/events verified redacted.
- [ ] Credential only lives in bounded memory scope.
- [ ] Raw provider response excluded unless explicitly sanitized.
- [ ] Test resource cleanup verified.

## Completion

- [ ] New test failed first for expected reason.
- [ ] Unit, contract, integration, and affected real-cloud smoke tests pass.
- [ ] Formatting/lint/typing/lock/secret gates pass.
- [ ] `rtk git diff --check` passes.
- [ ] Sprint evidence/status updated.
- [ ] Completion report cites fresh results and cleanup.
