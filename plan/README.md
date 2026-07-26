# OPS Scrum Delivery Plan

**Plan date:** 2026-07-17
**Canonical design:** CPS `docs/superpowers/specs/2026-07-16-openstack-cloud-provider-management-design.md`
**Cadence:** two-week sprints, aligned with CPS

## Product goal

Deliver OPS as a stateless, replay-safe OpenStack adapter that validates providers, discovers capabilities, collects normalized inventory, executes VM lifecycle operations, and publishes safe results to CPS without leaking OpenStackSDK objects or credentials.

## Working agreement

- CPS is the canonical contract/schema source; OPS pins a reviewed copy and checksum.
- OPS never adds SQLAlchemy, PostgreSQL, or other business database dependencies.
- Provider behavior is accessed through supported OpenStackSDK proxy/resource APIs.
- Compatibility is discovered per cloud; code does not lock to an OpenStack release.
- Handlers are safe under RabbitMQ at-least-once delivery and process restart.
- Tests precede implementation and include failure, retry, timeout, redaction, and replay paths.

## Definition of Ready

- CPS command/event schema and golden fixture are available or jointly agreed.
- Provider capability, expected terminal state, timeout, and retry class are defined.
- Required OpenStack resource/test account exists.
- Story fits one sprint and names CPS dependencies.

## Definition of Done

- Acceptance criteria and automated unit/contract/integration tests pass.
- Python 3.12 lock, lint, typing, formatting, secret scan, and build pass.
- SDK objects do not escape mapper/adapter boundaries.
- Logs/events are verified free of password, token, and `user_data`.
- Provider request IDs and normalized failures are observable.
- Code is reviewed and merged with no unresolved critical/high defect.

## Sprint roadmap

| Sprint | Goal | OPS increment | Paired CPS outcome |
|---|---|---|---|
| 0 | Reproducible stateless service | Python 3.12 project, health, RabbitMQ/OpenStack config, CI | Both repos build/test with pinned locks |
| 1 | Stable contract and messaging runtime | Pinned schemas, robust consumer/publisher, common errors/retries | Golden fixtures and topology interoperate |
| 2 | Provider connectivity | Provider onboarding validation, SDK connection, discovery | Provider validation operation completes end-to-end |
| 3 | Normalized inventory | Collectors, mappers, batching, targeted refresh | CPS safely reconciles all scoped resources |
| 4 | VM lifecycle | Create/detail/start/stop/reboot/delete and waiters | CPS operations and inventory converge |
| 5 | Recovery and release readiness | Replay safety, resilience, metrics, real-cloud acceptance | Restart/redelivery/drift suite passes |
| 6 | Design alignment and demo readiness | Capability, convergence, mapping, and image hardening | OPS behavior matches the approved first delivery |
| 7 | Scope and identity inventory | Effective scope discovery plus domain/project collectors | CPS reconciles administrative identity inventory |
| 8 | Identity lifecycle and quotas | Domain/project, assignment, and quota handlers | Disposable identity lifecycle passes through CPS |
| 9 | Network resource control | Network topology, security, router, port, and floating-IP handlers | Disposable topology is lifecycle-managed and cleaned |
| 10 | OpenStack tenant binding and ownership APIs | Explicit domain/project create handlers with CMP-owned bindings | CMP can request domain/project creation without inventory inference |
| 11 | Storage and provider catalog | Volume/snapshot, image, AZ, and flavor handlers | Storage/catalog operations converge and replay safely |
| 12 | Expanded control-plane release | Cross-resource replay, compatibility, and real-cloud acceptance | Recovery and provider compatibility matrix passes |
| 13 | Provider tenancy contract | Provider-owned credential resolution, project-owner normalization, authorization decision propagation | CPS authorizes tenant actions; OPS remains TMS/LMS-independent |

Sprints 7–13 remain proposed until the CPS design delta is approved and joint
Sprint Planning confirms contract readiness and capacity.

## Scrum artifacts

- Product Backlog: `plan/product-backlog.md`.
- Sprint Backlog: `plan/sprints/sprint-<n>.md`, created during joint planning.
- Contract readiness and integration risks are reviewed with CPS during refinement and Daily Scrum.
- Sprint Review demonstrates provider behavior through CPS whenever the paired slice exists.
