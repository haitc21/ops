# OPS-1203 — Provider creator role convergence

## Goal

After creating a Keystone domain or project, grant the provider credential
user the strongest supported administrative role at that exact scope.

## Design

- Resolve the user by the already-resolved provider username.
- Prefer `admin`, then supported administrator equivalents.
- Use Keystone IDs for role, user, domain and project assignments.
- Read existing assignments before creating one so retries converge safely.

## Acceptance

- Domain and project create operations assign the creator role.
- Replays remain idempotent.
- Missing user/role fails closed and emits a failed operation result.
- No TMS/LMS/BMS code changes.
