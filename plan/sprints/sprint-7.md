# Sprint 7 — OpenStack scope and identity inventory foundation

**Status:** Proposed — not started  
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
| OPS-701 Pin and validate scope/identity contracts | 5 | OPS | CPS-701 | Proposed |
| OPS-702 Effective scope discovery | 8 | OPS | CPS-702 validation contract | Proposed |
| OPS-703 Domain/project collectors and mappers | 8 | OPS | CPS-703 inventory contract | Proposed |

If confirmed OPS capacity is below 21 points, OPS-703 returns to Ready. Do not
partially implement identity collectors before the CPS inventory contract is
pinned.

## Delivery tasks

- [ ] Confirm CPS canonical contract readiness.
- [ ] Pin schemas, fixtures, and checksum byte-for-byte.
- [ ] Add failing scope-discovery and identity-collector tests.
- [ ] Implement supported-SDK effective scope discovery.
- [ ] Report per-operation identity capabilities and safe reasons.
- [ ] Add paginated domain/project collectors and primitive-only mappers.
- [ ] Add targeted refresh/tombstone behavior.
- [ ] Verify retry, timeout, replay, redaction, and provider request IDs.
- [ ] Run mocked RabbitMQ integration.
- [ ] Run read-only real-OpenStack identity acceptance.
- [ ] Update compatibility and operational documentation.
- [ ] Run the Definition of Done quality gates.

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
| CPS contract is not merged/pinned | CPS/OPS | Block OPS implementation after failing tests until canonical artifacts exist | Open |
| Provider policy hides domains | OPS | Test system/domain/project credential variants and report capability | Open |
| SDK auth scope differs by cloud | OPS | Use public APIs and a provider compatibility matrix | Open |
| Real-cloud credential is too privileged | Product Owner | Use dedicated read-only administrative test role | Open |
| Sprint 6 or dirty work overlaps handlers | OPS | Preserve existing work and isolate story changes after review | Open |

## Review evidence

- Demo scenario: resolve credential, discover scope, collect domains/projects,
  and publish batches accepted by CPS.
- Test commands and results:
- CPS checksum:
- OPS pinned checksum:
- OpenStack service/version/scope result:
- Known limitations:

## Retrospective actions

- Keep:
- Improve:
- One measurable action for next sprint:

