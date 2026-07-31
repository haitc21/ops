# OPS-1701 — Instance resize and rebuild convergence

**Status:** Done
**Active backlog:** No
**Paired task:** CPS-1701

## Outcome

OPS maps governed instance resize, confirm, revert, and rebuild commands to
OpenStackSDK and converges retry deliveries without duplicating mutations.

## Implementation

- Validate provider state before each action.
- Treat Nova intermediate states as convergence work on redelivery.
- Use `confirm_server_resize` and `revert_server_resize`, matching the
  OpenStackSDK compute proxy API.
- Return stable invalid-state errors and retry bounded asynchronous states.

## Evidence

- Unit coverage includes success, invalid state, timeout, and retry/redelivery.
- Live operations on `cmp-dev` completed resize, revert, rebuild, and final
  resize/confirm recovery through CPS and the OPS worker.
- CPS inventory returned `ACTIVE` with flavor `n1.small`; SSH succeeded after
  rebuild.
