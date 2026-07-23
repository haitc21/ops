# OPS AI Agent Rules

These instructions apply to every AI-assisted change in this repository. Priority order is: direct user request, this file, canonical CPS design/contracts, active Sprint Backlog, OPS product backlog, general conventions.

## Mandatory context before work

1. Read the canonical design in the sibling CPS repository: `../cps/docs/superpowers/specs/2026-07-16-openstack-cloud-provider-management-design.md`.
2. Read `plan/README.md`, `plan/product-backlog.md`, and active `plan/sprints/sprint-<n>.md` when Sprint Planning has created one.
3. Identify the OPS story and paired CPS contract/story.
4. Check worktree status and preserve unrelated changes.
5. Follow `docs/ai/vibe-coding-workflow.md` and `docs/ai/review-checklists.md`.

Do not implement backlog work outside the active sprint without an explicit priority change.

## CodeGraph-first discovery

When the CMP workspace root contains `.codegraph/`, query it before `rg`, directory traversal, or manual code reading:

```powershell
codegraph explore "<focused symbol or behavior question>"
codegraph node <symbol-or-file>
```

Use returned source and call paths to assess consumers and blast radius. Use `rg` for exact strings/config/non-code assets or when the index lacks the new repository code. State when the index is stale; do not infer missing relationships. Do not create/re-index CodeGraph unless requested.

## RTK command policy

Prefix supported external commands with `rtk`:

```powershell
rtk git status --short
rtk rg "pattern" src tests
rtk pytest -q
rtk docker compose ps
```

RTK does not resolve PowerShell cmdlets such as `Get-ChildItem` or `Get-Content`; use those cmdlets directly when needed. Fall back to another direct external command only if RTK is unavailable, blocked, or changes semantics, and record why. Never run `rtk init -g` without explicit authorization. Always check command exit status.

## Architectural boundaries

- OPS is a stateless OpenStack adapter. It has no business database, SQLAlchemy, Alembic, or PostgreSQL dependency.
- CPS owns common contracts, provider configuration, credentials, inventory, operations, and durable truth.
- OPS resolves credentials just in time, retains them only in memory, and never publishes/logs/caches them.
- OpenStack access uses supported OpenStackSDK connection/proxy/resource APIs. Do not add direct Nova/Neutron/Cinder clients or deprecated `python-openstacksdk`.
- SDK objects never leave the OpenStack adapter/mapper boundary.
- Compatibility is discovered per connection; never lock behavior to an OpenStack named release.
- RabbitMQ commands may be delivered more than once. Every handler must be replay-safe using provider state, operation identity, and preconditions.
- Full reconciliation is the convergence safety net; targeted refresh never infers deletion from timeout/auth/service failure.

## Pinned contract rule

CPS is canonical. For every boundary change:

1. Confirm the CPS schema, fixture, version, and checksum exist.
2. Update the pinned OPS copy without provider-specific drift.
3. Add failing contract/mapper/handler tests.
4. Reject unknown major versions and tolerate approved additive fields.
5. Publish only validated common resources/errors/events.

Never independently invent common fields in OPS. Propose them in CPS first.

## Test-driven implementation

Use red-green-refactor. A provider feature starts with SDK fake/mocked HTTP tests for successful and failure behavior. Then add RabbitMQ integration tests. Real-cloud tests validate compatibility last; they do not replace deterministic unit/contract tests.

Bug fixes require regression tests. Do not bypass waiters, weaken error assertions, or hide provider failures to make tests green.

## OpenStack implementation rules

- Create a fresh scoped connection/session per unit of work or a clearly bounded non-secret session lifecycle.
- Apply auth URL, username/password, user/project domains, project, region, interface, TLS, CA, timeouts, and user agent explicitly.
- Discover service catalog and supported API/microversions; return unsupported reasons.
- Use generator pagination and tolerate optional/missing fields.
- Separate collectors, mappers, operations, waiters, and error normalization.
- Use SDK waiters or a deterministic bounded waiter abstraction; never poll indefinitely.
- Preserve safe provider request IDs. Do not expose raw response bodies indiscriminately.
- Delegate root-volume deletion to Nova `delete_on_termination`; never blindly issue a second Cinder delete.
- Explicit networks are required for create. Validate referenced resources in the same project/connection.
- Treat `user_data`, token, password, authorization header, and private key material as secrets.

## Messaging, retry, and shutdown

- Validate envelope before any provider mutation.
- Publish progress/result with confirms before acknowledging the command.
- Bound prefetch, per-service concurrency, retries, backoff, and total deadlines.
- Retry only classified transient failures. Check provider state before retrying a mutation.
- Duplicate create must search the operation/idempotency marker before creating.
- Duplicate power/delete must inspect current state and republish a safe result.
- Graceful shutdown stops intake, finishes or nacks in-flight work, and closes RabbitMQ/SDK resources.
- Poison/unsupported messages terminate in DLQ rather than looping.

## Python and dependencies

- Use CPython 3.12 and the OPS lockfile.
- Core runtime includes FastAPI/Pydantic, aio-pika, OpenStackSDK, and minimal HTTP/observability support.
- Do not add CPS persistence dependencies.
- Prefer supported public SDK APIs. Adding a dependency requires rationale, security/maintenance review, and lockfile verification.

## Git and completion discipline

- Keep changes story-scoped and commits reviewable.
- AI agents must not stage, commit, amend, merge, rebase, or push unless the user explicitly requests that exact Git operation in the current turn. Authorization from an earlier turn does not carry forward.
- A plan step named `Commit` means prepare a commit proposal and stop for approval; it does not authorize `git add` or `git commit`. Requests such as "continue", "finish", or "execute the plan" do not imply Git authorization.
- Preserve unrelated work; never reset/discard it.
- Never commit real credentials, tokens, `clouds.yaml`, private keys, `.env`, generated cache, or captured provider payloads.
- Run focused then full quality gates with RTK and report exact outcomes.
- Use real OpenStack only with explicit test resources and cleanup verification.
- Update active Sprint Backlog evidence/status before declaring Done.
- Do not claim compatibility, replay safety, or completion without fresh tests.
