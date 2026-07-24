# Sprint 7 — OpenStack scope and identity inventory foundation

**Status:** Complete (implementation and review finished 2026-07-24)  
**Dates:** 2026-07-27 to 2026-08-07  
**Capacity:** Confirm at Sprint Planning; proposed 21 OPS points  
**Sprint Goal:** Discover effective OpenStack administrative scope and publish
normalized domain/project inventory that CPS can reconcile safely, without
performing identity mutations.

**Canonical CPS design:**  
`../../../cps/docs/superpowers/specs/2026-07-24-openstack-resource-control-plane-expansion-design.md`

**Canonical CPS plan:**  
`../../../cps/docs/superpowers/plans/2026-07-24-openstack-resource-control-plane-expansion.md`

## Selected stories

| Story | Points | Owner | CPS dependency | Status |
|---|---:|---|---|---|
| OPS-701 Pin and validate scope/identity contracts | 5 | OPS | CPS-701 | Done |
| OPS-702 Effective scope discovery | 8 | OPS | CPS-702 validation contract | Done |
| OPS-703 Domain/project collectors and mappers | 8 | OPS | CPS-703 inventory contract | Done |

If confirmed OPS capacity is below 21 points, OPS-703 returns to Ready. Do not
partially implement identity collectors before the CPS inventory contract is
pinned.

## Delivery tasks

- [x] Confirm CPS canonical contract readiness.
- [x] Pin schemas, fixtures, and checksum byte-for-byte.
- [x] Add failing scope-discovery and identity-collector tests.
- [x] Implement supported-SDK effective scope discovery.
- [x] Report per-operation identity capabilities and safe reasons.
- [x] Add paginated domain/project collectors and primitive-only mappers.
- [x] Add targeted refresh/tombstone behavior.
- [x] Verify retry, timeout, replay, redaction, and provider request IDs.
- [x] Run mocked RabbitMQ integration.
- [x] Run read-only real-OpenStack identity acceptance.
- [x] Update compatibility and operational documentation.
- [x] Run the Definition of Done quality gates.

## Story acceptance

### OPS-701

- All CPS artifacts match the pinned checksum.
- Semantic validation rejects malformed scope/owner combinations.
- Existing command/event fixtures remain valid.

### OPS-702

- Effective system/domain/project scope uses public OpenStackSDK/session APIs.
- OPS does not infer admin status from username or configured label.
- Missing system-scope support is explicit and does not affect readiness.
- No raw token or catalog leaves in-memory connection scope.

### OPS-703

- Domains and projects paginate and tolerate missing optional fields.
- Forbidden domain collection reports `SKIPPED_UNSUPPORTED` or normalized
  authorization failure according to the CPS contract.
- Project collection remains available to a project-scoped connection.
- Mapper output contains only safe JSON primitives.
- Duplicate/redelivered collection produces equivalent provider identities and
  checksums.
- Targeted NotFound is the only provider response that emits a tombstone.

## Risks and impediments

| Risk/impediment | Owner | Mitigation | Status |
|---|---|---|---|
| CPS contract is not merged/pinned | CPS/OPS | CPS artifacts are pinned byte-for-byte | Resolved |
| Provider policy hides domains | OPS | Capability reasons are explicit and project collection remains safe | Accepted |
| SDK auth scope differs by cloud | OPS | Public SDK/session scope discovery with UNKNOWN fallback | Resolved |
| Real-cloud credential is too privileged | Product Owner | Read-only validation path exercised | Accepted |
| Sprint 6 or dirty work overlaps handlers | OPS | Existing work preserved and targeted gates pass | Resolved |

## Review evidence

- Demo scenario: resolve credential, discover scope, collect domains/projects,
  and publish batches accepted by CPS.
- Test commands and results: OPS `352 passed, 24 skipped`; CPS `485 passed, 193 skipped`; CPS DB integration `146 passed`; contract semantic validation passed.
- CPS checksum: OPS pinned manifest matches CPS byte-for-byte.
- OPS pinned checksum: schema and fixtures verified with SHA-256.
- OpenStack service/version/scope result: live validation succeeded against OpenStackSDK 6.6.0; effective scope `PROJECT`, with safe `SYSTEM_SCOPE_REQUIRED` reasons.
- Known limitations: live inventory is blocked by catalog endpoints advertising `controller` that refuse connections from the Compose network; all collector/retry/tombstone/replay behavior is covered by tests.

## Retrospective actions

- Keep: primitive-only mappers and explicit capability reasons.
- Improve: validate service-catalog reachability in deployment preflight.
- One measurable action for next sprint: add endpoint reachability checks to OPS startup diagnostics.
