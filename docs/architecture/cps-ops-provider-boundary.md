# OPS provider boundary

OPS is not an end-user authentication service. CPS validates the Keycloak JWT
and applies CMP `admin`/`member` plus organization/workspace policy before an
internal command reaches OPS.

OPS uses the single configured OpenStack administrator credential for every
provider operation. It does not inspect BMS, Keycloak, TMS, or end-user role
claims. Its responsibility is OpenStack SDK execution, waiters, retries,
provider error normalization, ownership/state rechecks supplied by CPS, and
safe convergence.

Horizon/OpenStack SDK code is a provider-behavior reference only. Do not copy
Horizon session or UI authentication assumptions into OPS. Never persist JWTs,
authorization headers, provider credentials, or bearer URLs in command/result
payloads or logs.
