# OPS-1901 Catalog Mapper and Capability Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Task ID:** OPS-1901

**Goal:** Pin CPS-1901 contracts and emit complete, bounded, deterministic image/flavor inventory and capability data without leaking SDK objects, raw provider bodies, or secrets.

**Architecture:** OPS remains a stateless OpenStack adapter. It copies the reviewed CPS canonical schema/fixtures/checksum, maps OpenStackSDK image/flavor resources into plain validated contract dictionaries, performs bounded enrichment through supported proxy APIs, and reports operation-level capabilities with explicit reasons. Any incomplete required enrichment fails or skips the collection safely; it never publishes a misleading successful empty collection or tombstone.

**Tech Stack:** CPython 3.12, Pydantic v2, aio-pika, OpenStackSDK 4.17.0, pytest SDK fakes/mocked HTTP, Ruff, MyPy.

## Approved Inputs and Context

- Sprint/design approval for OPS-1901 and paired CPS-1901 is granted by the user on 2026-08-01.
- Authoritative repository rules: `AGENTS.md`.
- Canonical CPS designs:
  - `../cps/docs/superpowers/specs/2026-07-16-openstack-cloud-provider-management-design.md`
  - `../cps/docs/superpowers/specs/2026-07-24-openstack-resource-control-plane-expansion-design.md`
  - `../cps/docs/superpowers/specs/2026-07-26-provider-tenancy-authorization-design.md`
- Delivery policy: `plan/README.md`, `plan/product-backlog.md`, `docs/ai/vibe-coding-workflow.md`, and `docs/ai/review-checklists.md`.
- Active work packet: `plan/sprints/sprint-19.md` and `plan/tasks/sprint-19/OPS-1901-catalog-mappers.md`.
- Predecessor: `plan/tasks/sprint-17/OPS-1703-curated-catalog-inventory.md`.
- Paired canonical plan: `../cps/docs/superpowers/plans/2026-08-01-CPS-1901-catalog-contracts.md`.
- Behavioral references only:
  - `../opensource/horizon/openstack_dashboard/api/glance.py`
  - `../opensource/horizon/openstack_dashboard/api/nova.py`
  - Horizon image/flavor tables named by Sprint 19
- Supported SDK references inspected:
  - `../opensource/openstacksdk/openstack/image/v2/image.py`
  - `../opensource/openstacksdk/openstack/image/v2/_proxy.py`
  - `../opensource/openstacksdk/openstack/compute/v2/flavor.py`
  - `../opensource/openstacksdk/openstack/compute/v2/_proxy.py`
  - their unit tests under `../opensource/openstacksdk/openstack/tests/unit/`
- CodeGraph blast radius:
  - `map_resource` has callers in inventory collection and instance handlers; preserve non-catalog behavior.
  - `collect_resources` is called by the inventory handler and tests; keep its default-compatible signature.
  - Capability discovery flows through `discover_capabilities` into connection validation and CPS persistence.
- Initial worktrees are clean at `b843029` (OPS) and `81c1b02` (CPS). Recheck before execution.

## Acceptance Criteria

1. OPS contract schema, full/minimal image/flavor fixtures, and pinned CPS manifest match the approved CPS-1901 canonical bytes and checksums.
2. OPS accepts additive major-1 fields and rejects unknown major versions before OpenStack access.
3. Image mapping emits owner, status, protection, container/disk format, size/virtual size, tags, bounded properties, checksum, minima, visibility, and approval.
4. Flavor mapping emits vCPUs, RAM, root/ephemeral disk, swap, public/enabled state, bounded extra specs, project access IDs, and approval.
5. SDK objects, exceptions, bytes, direct URLs, locations, raw service catalogs, and secret-bearing metadata never cross the mapper boundary.
6. Image collection uses one paginated SDK generator and no per-row call for contract fields.
7. Flavor collection uses one paginated detail generator, reuses inline extra specs, enriches only missing extra specs/private access, and never exceeds the configured call budget.
8. Full and targeted results sort deterministically by provider resource ID; optional/missing fields and unusual numeric values normalize safely.
9. Partial enrichment failure, service absence, forbidden access, timeout, concurrent disappearance, and targeted 404 have explicit non-destructive outcomes.
10. Capability discovery reports explicit supported/unsupported plus reason for image import/member/deactivate/reactivate and flavor create/delete/access/extra specs without release-name checks.
11. Focused/full gates, independent review, task-diff secret scan, live CPS-triggered inventory, OpenStack CLI field comparison, zero-resource cleanup proof, redacted runbook, and Git gates pass.

## Out of Scope

- Image/flavor mutations or RabbitMQ mutation handlers; OPS-1902/OPS-1903 own them.
- Image bytes, upload/staging streams, URL import execution, signed/private source credentials, or provider location exposure.
- Compatibility arithmetic; CPS-1901 owns provider-neutral compatibility decisions.
- Direct novaclient/glanceclient/Cinder client use, SDK internals, raw HTTP calls, named OpenStack release checks, or new runtime dependencies.
- A database, local cache, durable mapper state, or changes to inventory deletion/finalization semantics.
- Refactoring unrelated resource mappers or changing instance/network/storage behavior.

## Contract Pin and Mapping Decisions

### Pin order and version

1. CPS Task 2 produces reviewed canonical version `1.1` artifacts.
2. OPS copies the exact CPS `inventory_batch.schema.json`, four image/flavor fixtures, `capability_document.schema.json`, and canonical manifest entries.
3. OPS updates its local manifest and `cps_checksums.pinned.json`; `test_pin_against_cps.py` proves byte identity.
4. No OPS-only common field or provider-specific drift is permitted.
5. Major `1` remains accepted; unknown major versions fail before connection creation/provider access.

### Exact SDK field map

| Canonical field | OpenStackSDK source | Normalization |
|---|---|---|
| image identity/name/status | `Image.id`, `name`, `status` | ID/name strings; lowercase status |
| image owner | `owner` then `owner_id` | `project_provider_resource_id` |
| image visibility | `visibility` | lowercase enum |
| image protection | `is_protected` then `protected` | nullable boolean |
| image formats | `container_format`, `disk_format` | lowercase strings |
| image sizes/minima | `size`, `virtual_size`, `min_disk`, `min_ram` | nonnegative integers or null |
| image checksum | `checksum` | bounded string |
| image tags | `tags` | sorted unique strings, maximum 64 |
| image properties | `properties` | bounded/sanitized map; never `file`, `locations`, `direct_url`, `url`, `data`, or SDK internals |
| image approval | property or tag `cmp-catalog-approved=true` | strict boolean; missing/malformed false |
| flavor identity/name | `Flavor.id`, `name` | strings |
| flavor dimensions | `vcpus`, `ram`, `disk`, `ephemeral`, `swap` | bounded integers; empty swap becomes 0 |
| flavor visibility/state | `is_public`, inverse of `is_disabled` | nullable booleans |
| flavor extra specs | inline `extra_specs`, otherwise `fetch_flavor_extra_specs` | bounded/sanitized sorted map |
| flavor access | `get_flavor_access` for private flavors | sorted unique `tenant_id`, maximum 256 |
| flavor approval | `extra_specs["cmp-catalog-approved"]` | strict boolean; missing/malformed false |

All mapped output is validated by the pinned `InventoryBatchItem` before publication.

### Bounds and secret rejection

- Metadata map: at most 128 entries, key at most 255 characters, nested depth at most 4, serialized field at most 64 KiB.
- Tags: at most 64 entries, each at most 255 characters.
- Access project IDs: at most 256 entries, each at most 255 characters.
- Reject/drop keys containing `password`, `token`, `authorization`, `private_key`, `user_data`, `ca_cert`, `secret`, or `credential`, case-insensitively at every depth.
- Reject/drop values that are bytes, SDK/resource objects without a plain allow-listed identity, URLs containing userinfo, or URLs/query keys carrying signatures/tokens/credentials.
- Never map `file`, `locations`, `direct_url`, `url`, image `data`, session/auth objects, service catalog, request/response, headers, or exception bodies.
- Exceeding a collection-level map/bounds invariant fails validation and prevents publication; it is not silently truncated except for the contract-approved list maxima, which record `metadata_truncated=true`.

## Enrichment and Call-Budget Decision

Add `Settings.catalog_enrichment_max_calls: int = 256` with accepted range `1..4096`.

### Full image collection

- One call to `connection.image.images()`; consume the SDK generator under the existing total timeout.
- No per-image base-resource fetch is permitted for fields in OPS-1901.
- **Approved review correction (cycle 2):** Glance membership is not carried on
  the Image resource. For images whose provider visibility is `shared`, call
  the supported `connection.image.members(image)` generator once, map only
  `Member.member_id`, and charge the same shared enrichment budget before the
  call. Public/private/community images make no membership call. Any member
  enrichment error prevents an empty successful batch.
- Preserve generator behavior and sort mapped output by `provider_resource_id` only after collection.

### Full flavor collection

- One call to `connection.compute.flavors(details=True, get_extra_specs=False)`.
- Reuse inline `extra_specs` when present.
- For each flavor missing specs, call `connection.compute.fetch_flavor_extra_specs(flavor)` once.
- For each private flavor, call `connection.compute.get_flavor_access(flavor)` once.
- Increment the shared collection budget before each enrichment request. If the next request would exceed 256, raise `CatalogEnrichmentBudgetExceeded`; publish no `COMPLETE` flavor batch.
- Public flavors do not call `get_flavor_access`; their access list is empty.
- Deduplicate/sort access IDs and extra-spec keys before mapping/checksum.

### Targeted collection

- Image: `connection.image.get_image(id)`, followed by one budgeted
  `connection.image.members(image)` call only when visibility is `shared`.
- Flavor: `connection.compute.get_flavor(id, get_extra_specs=False)`, then at most one missing-spec and one private-access call under the same budget.
- A direct getter `ResourceNotFound` is the only condition that creates a targeted tombstone.
- A 404/timeout/forbidden response after the base resource was fetched is an enrichment failure, not a tombstone.

### Failure outcomes

| Condition | Outcome |
|---|---|
| Optional image/flavor service absent | `SKIPPED_UNSUPPORTED`; no items; full sync cannot infer deletion |
| Explicit forbidden enrichment/capability | `SKIPPED_UNSUPPORTED` with safe reason in capability evidence; no empty `COMPLETE` batch |
| Timeout, 429, selected 5xx, network failure, budget exceeded | retryable handler result; no batch/completed event |
| Item disappears during full-list enrichment | retry the collection once within existing handler retry policy; persistent disappearance omits only provider-confirmed absent item on the next fresh list |
| Targeted base getter 404 | one validated tombstone |
| Targeted enrichment failure after base getter | retryable/unsupported result; never tombstone |
| Mapper validation or contract mismatch | permanent contract failure/DLQ; no provider mutation and no unsafe log body |

## Capability Decision

Add exact feature keys:

```text
image.import
image.member
image.deactivate
image.reactivate
flavor.create
flavor.delete
flavor.access
flavor.extra_specs
```

- Start with service availability.
- Check supported public proxy methods:
  - `image.get_import_info` plus `image.import_image`
  - `image.add_member` plus `image.members`
  - `image.deactivate_image`
  - `image.reactivate_image`
  - `compute.create_flavor`
  - `compute.delete_flavor`
  - `compute.get_flavor_access`, `flavor_add_tenant_access`, and `flavor_remove_tenant_access`
  - `compute.fetch_flavor_extra_specs`, create/update/delete extra-spec methods
- Image import is supported only when `get_import_info()` returns at least one safe import method. Return only method names, never endpoint/token/catalog data.
- Capability reasons are exactly `SERVICE_NOT_AVAILABLE`, `CAPABILITY_NOT_SUPPORTED`, `PROVIDER_FORBIDDEN`, or `DISCOVERY_FAILED`.
- A discovery read is bounded by `openstack_timeout_seconds`; failure affects that feature, not OPS readiness and not unrelated services.
- Do not infer capability from OpenStack release names or username/role names.

## Threat Model and Security Scope

### Assets and boundaries

- Assets: provider credentials in memory, canonical contract integrity, project access lists, safe catalog metadata, capability truth, and deterministic checksums.
- Boundaries: CPS command to OPS envelope validation, credential resolution to SDK session, SDK resource to mapper, mapper to Pydantic contract, and publisher-confirmed result to CPS.
- Attacker-controlled inputs: provider metadata/tags/extra specs/access IDs, malformed SDK values, service catalog/version responses, contract versions, and redelivered commands.

### Required invariants

- Credentials/tokens live only in bounded connection scope and never enter mapper output, logs, fixtures, exceptions, capability documents, or runbooks.
- SDK objects and raw HTTP bodies stop at the adapter boundary.
- Approval/access cannot be forged by a request; values come from provider reads and strict normalization.
- Collection/enrichment calls, timeouts, item counts, metadata depth/bytes, and retry behavior are bounded.
- Partial failure never becomes successful empty inventory or deletion evidence.
- Unknown major contracts fail before provider access.
- Sorting and checksums remain deterministic across redelivery.

### Abuse cases to test

- Nested sensitive key, mixed-case key, SDK object, bytes, credential-bearing URL, signed URL, and oversized metadata.
- Malicious `cmp-catalog-approved` values such as object, integer, or misleading string.
- More than 256 enrichment calls and more than 256 private access IDs.
- Provider returns duplicate/unsorted tags, access rows, or resources.
- Flavor swap values `""`, `"0"`, integer, negative, and nonnumeric.
- Image Docker/raw, private/shared/community visibility, missing optional fields, and unsupported formats.
- 401/403/404/409/429/5xx, timeout, service absence, capability discovery failure, and concurrent deletion.
- Result publish failure/redelivery produces identical mapped data/checksum and no additional mutation.

Unresolved Critical/High findings block live acceptance, completion, commit, and push.

## Exact File Scope

### Contract pin files

- Create from the CPS implementation: `src/ops/contracts/safe_metadata.py`
- Modify: `src/ops/contracts/messages/inventory.py`
- Create from CPS bytes: `src/ops/contracts/jsonschema/inventory_batch.schema.json`
- Create from CPS bytes: `src/ops/contracts/fixtures/events/inventory_batch_image_full.json`
- Create from CPS bytes: `src/ops/contracts/fixtures/events/inventory_batch_image_minimal.json`
- Create from CPS bytes: `src/ops/contracts/fixtures/events/inventory_batch_flavor_full.json`
- Create from CPS bytes: `src/ops/contracts/fixtures/events/inventory_batch_flavor_minimal.json`
- Modify from CPS bytes: `src/ops/contracts/jsonschema/capability_document.schema.json`
- Modify: `src/ops/contracts/checksums.json`
- Modify: `src/ops/contracts/cps_checksums.pinned.json`
- Modify: `src/ops/contracts/validation.py`

### Production files

- Modify: `src/ops/config.py`
- Modify: `src/ops/openstack/inventory.py`
- Modify: `src/ops/openstack/discovery.py`
- Modify: `src/ops/application/handlers/inventory_collect.py`

### Test files

- Modify: `tests/contract/test_pin_against_cps.py`
- Modify: `tests/contract/test_contract_manifest.py`
- Modify: `tests/contract/test_contract_semantics.py`
- Modify: `tests/unit/openstack/test_inventory.py`
- Modify: `tests/unit/openstack/test_discovery.py`
- Modify: `tests/unit/application/test_connection_validate.py`
- Create: `tests/unit/test_config.py`

### Completion evidence

- Create at completion: `docs/runbooks/sprint-19-catalog-mappers.md`
- Modify at completion: `plan/tasks/sprint-19/OPS-1901-catalog-mappers.md`
- Modify at completion: `plan/sprints/sprint-19.md`
- Link at completion from: `../cps/docs/runbooks/sprint-19-catalog-contracts.md`

No dependency or lockfile changes are expected. Any file outside this list requires explicit re-planning before modification.

**Approved execution correction (2026-08-02):** CPS commit `b8a5ff7` made the
canonical inventory and capability consumer models depend on its new bounded
`safe_metadata` helper. The Planner approved pinning the corresponding OPS
helper file instead of duplicating those validators into `inventory.py`.
`.secrets.baseline` is also updated narrowly so canonical inventory checksum
fixtures remain recognized as generated hashes rather than secrets.

---

### Task 1: Isolated Worktree, CPS Contract Gate, and Baseline

**Files:** No tracked file changes.

**Interfaces:** Establishes a clean OPS execution base and immutable CPS pin source.

- [ ] **Step 1: Confirm CPS contract readiness**

Require the reviewed CPS Task 2 diff/commit, canonical manifest hash, and passing CPS contract validation. Do not invent fields while CPS is still changing.

- [ ] **Step 2: Invoke execution isolation**

Invoke `superpowers:using-git-worktrees`, then create an OPS-1901 worktree and task branch without touching the current checkout.

- [ ] **Step 3: Recheck repository state**

Run:

```bash
rtk git status --short
rtk git log -5 --oneline
```

Expected: clean task worktree and approved Sprint 19 planning at HEAD.

- [ ] **Step 4: Invoke execution workflow**

Invoke `superpowers:subagent-driven-development` (preferred) or `superpowers:executing-plans`, then invoke `superpowers:test-driven-development`.

- [ ] **Step 5: Record baseline**

Run:

```bash
rtk pytest -q tests/contract/test_pin_against_cps.py tests/unit/openstack/test_inventory.py tests/unit/openstack/test_discovery.py
rtk ruff check src tests
rtk mypy src
```

Expected: baseline passes. Diagnose unrelated failure before changing assertions.

### Task 2: Pin CPS-1901 Contract Artifacts Exactly

**Files:** All contract pin and contract test files listed in Exact File Scope.

**Interfaces:**
- Consumes reviewed CPS canonical bytes and manifest.
- Produces the exact OPS consumer models/fixtures/schema/pin required by mapper and publisher work.

- [ ] **Step 1: Write RED pin/compatibility tests**

Assert the new artifact paths exist, bytes/checksums match CPS, full/minimal image/flavor fixtures validate, additive `1.1` fields pass, unknown major fails, and required capability keys are present.

- [ ] **Step 2: Observe RED**

Run:

```bash
rtk pytest -q tests/contract/test_pin_against_cps.py tests/contract/test_contract_manifest.py tests/contract/test_contract_semantics.py
```

Expected: FAIL because the new CPS artifacts and enriched consumer model are not pinned.

- [ ] **Step 3: Copy canonical artifacts and minimally update consumer model**

Copy, do not reformat, CPS JSON Schema and fixture bytes. Mirror only canonical Pydantic field definitions/validators needed to validate the pin. Add no OPS-specific common field.

- [ ] **Step 4: Regenerate local manifest and verify pin**

Run:

```bash
python -m ops.contracts.write_manifest
python -m ops.contracts.validate_contracts
rtk pytest -q tests/contract/test_pin_against_cps.py tests/contract/test_contract_manifest.py tests/contract/test_contract_semantics.py
sha256sum \
  src/ops/contracts/jsonschema/inventory_batch.schema.json \
  ../cps/src/cps/contracts/jsonschema/inventory_batch.schema.json
```

Expected: contract tests PASS and corresponding CPS/OPS artifact hashes match.

- [ ] **Step 5: Prepare commit boundary**

Prepare, but do not execute, proposal:

```text
chore(ops): pin CPS-1901 catalog contracts
```

Include only Task 2 files.

### Task 3: RED Image/Flavor Mapper and Sanitizer Coverage

**Files:**
- Modify: `tests/unit/openstack/test_inventory.py`
- Modify: `src/ops/openstack/inventory.py`

**Interfaces:**
- Produces plain dictionaries accepted by pinned `InventoryBatchItem`.
- Preserves all existing non-image/flavor mapping behavior.

- [ ] **Step 1: Write RED full/minimal mapper tests**

Add table-driven tests for the exact field map, missing optional fields, owner aliases, statuses, all visibilities, protection aliases, size/virtual size/minima, Docker/raw, tags/properties, unusual swap values, extra specs, private access, and strict approval normalization.

- [ ] **Step 2: Write RED sanitizer abuse tests**

Cover every forbidden key fragment/value class, nested depth, list/map/string/count/byte bounds, SDK objects, bytes, credential-bearing URLs, signed URLs, duplicate tags/access IDs, and deterministic key/list order.

- [ ] **Step 3: Observe RED**

Run:

```bash
rtk pytest -q tests/unit/openstack/test_inventory.py -k "image or flavor or catalog or sanit"
```

Expected: FAIL on missing enriched fields, bounds, strict secret-value rejection, and deterministic access/spec normalization.

- [ ] **Step 4: Implement minimal GREEN mappers**

Keep `map_resource(resource_type, resource)` compatible for all callers. Add focused private helpers for image and flavor only; return contract field names from the approved map. Validate each result with `InventoryBatchItem` in batch construction.

- [ ] **Step 5: Verify GREEN**

Rerun the focused command, then:

```bash
rtk pytest -q tests/unit/openstack/test_inventory.py
```

Expected: all inventory tests PASS and existing resource snapshots remain unchanged.

- [ ] **Step 6: Refactor while GREEN**

Share only sanitizer/bounds utilities used by both catalog mappers; do not split or redesign unrelated resource mapping.

- [ ] **Step 7: Prepare commit boundary**

Prepare, but do not execute, proposal:

```text
feat(ops): map bounded image and flavor details
```

Include only Task 3 files.

### Task 4: RED Bounded Collection and Failure Semantics

**Files:**
- Modify: `src/ops/config.py`
- Modify: `src/ops/openstack/inventory.py`
- Modify: `src/ops/application/handlers/inventory_collect.py`
- Modify: `tests/unit/openstack/test_inventory.py`
- Create: `tests/unit/test_config.py`

**Interfaces:**
- Extends `collect_resources` and `collect_targeted_resource` with keyword-only enrichment budget while preserving existing callers.
- Produces explicit complete/skipped/retryable/tombstone outcomes through the inventory handler.

- [ ] **Step 1: Write RED call-budget tests**

Use counting fakes to assert one image generator call, one flavor generator call, no public-flavor access calls, inline-spec reuse, one missing-spec call, one private-access call, exact limit enforcement at 256, and stable output ordering.

- [ ] **Step 2: Write RED failure matrix**

Cover service absence, forbidden enrichment, timeout, 429/5xx, budget exceeded, partial enrichment, concurrent disappearance, stable redelivery checksum, direct targeted 404 tombstone, and post-get enrichment failure without tombstone.

- [ ] **Step 3: Observe RED**

Run:

```bash
rtk pytest -q tests/unit/openstack/test_inventory.py tests/unit/test_config.py
```

Expected: FAIL because budget configuration, enrichment flow, and explicit failure classification are absent.

- [ ] **Step 4: Implement minimal budgeted collectors**

Add `catalog_enrichment_max_calls=256`, a local counter object, and catalog-specific collector helpers. Pass the setting from the handler without changing unrelated collection defaults. Keep all SDK calls inside the existing `asyncio.to_thread` plus total timeout boundary.

- [ ] **Step 5: Implement safe failure classification**

Return no `COMPLETE` batch after incomplete required enrichment. Preserve `SKIPPED_UNSUPPORTED` only for permanent service/capability/permission absence; transient conditions return `HandlerRetryableError`; direct targeted base 404 remains the sole tombstone source.

- [ ] **Step 6: Verify GREEN**

Run:

```bash
rtk pytest -q tests/unit/openstack/test_inventory.py tests/unit/test_config.py
rtk pytest -q tests/unit/application -k inventory
```

Expected: PASS, with exact SDK call counts and no unsafe empty-success path.

- [ ] **Step 7: Prepare commit boundary**

Prepare, but do not execute, proposal:

```text
feat(ops): bound catalog enrichment and failures
```

Include only Task 4 files.

### Task 5: RED Capability Discovery

**Files:**
- Modify: `src/ops/contracts/validation.py`
- Modify: `src/ops/openstack/discovery.py`
- Modify: `tests/unit/openstack/test_discovery.py`
- Modify: `tests/unit/application/test_connection_validate.py`

**Interfaces:**
- Produces the eight canonical capability entries and safe reasons.
- Connection validation publishes only validated `CapabilityDocument`.

- [ ] **Step 1: Write RED capability matrix**

Test service absent, method absent, all methods present, import methods empty/present, forbidden import-info read, discovery timeout/failure, and safe serialization without endpoints/tokens/raw catalog.

- [ ] **Step 2: Observe RED**

Run:

```bash
rtk pytest -q tests/unit/openstack/test_discovery.py tests/unit/application/test_connection_validate.py
```

Expected: FAIL because the eight capabilities and import-info discovery are absent.

- [ ] **Step 3: Implement minimal GREEN discovery**

Use only the supported proxy methods listed in this plan. Bound safe discovery reads, preserve unrelated service capabilities, and return one of the four approved reason codes.

- [ ] **Step 4: Verify GREEN**

Rerun Task 5 tests and validate serialized documents with the pinned capability JSON Schema.

Expected: PASS with no raw catalog/token/endpoint leakage in feature extras.

- [ ] **Step 5: Refactor while GREEN**

Use a small declarative method-requirement table only if reason handling remains explicit and covered per feature.

- [ ] **Step 6: Prepare commit boundary**

Prepare, but do not execute, proposal:

```text
feat(ops): discover catalog operation capabilities
```

Include only Task 5 files.

### Task 6: Independent Review and Review Remediation

**Files:** Complete OPS-1901 task diff only.

- [ ] **Step 1: Request independent review**

Invoke `superpowers:requesting-code-review`. Dispatch Codex ChatGPT 5.6 luna for two explicit passes:

1. CPS pin/schema/checksum and acceptance compliance; and
2. mapper quality, SDK API support, optional-field behavior, call budgets, timeout/failure semantics, deterministic checksums, secret redaction, and tests.

Require severity plus file/line evidence.

- [ ] **Step 2: Receive findings rigorously**

Invoke `superpowers:receiving-code-review`. Validate every finding technically. Fix valid findings through RED-GREEN-REFACTOR and document evidence for rejected findings.

- [ ] **Step 3: Run affected and full automated gates**

Run:

```bash
rtk ruff check src tests
rtk mypy src
rtk pytest -q
python -m ops.contracts.validate_contracts
rtk git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 4: Require luna re-approval**

Send the final diff and fresh outputs to the same reviewer. Do not proceed with unresolved finding or missing final approval.

### Task 7: Secret Scan

**Files:** Final OPS-1901 Git diff only.

- [ ] **Step 1: Run repository secret scan**

Run `detect-secrets scan --all-files`. Dedicated Codex Security skill gates were removed from the global workflow by explicit user direction; Luna's independent review remains required.

- [ ] **Step 2: Review sensitive-data paths**

Trace provider metadata/URL/SDK object to mapper/contract/log, credential resolution to connection lifetime, CPS command version to provider access, partial enrichment to batch completion/deletion safety, and capability response to persisted CPS document.

- [ ] **Step 3: Triage findings**

Fix/retest valid findings and track only policy-permitted residuals. Any unresolved Critical/High finding blocks live acceptance and Git completion.

- [ ] **Step 4: Preserve safe evidence**

Record only IDs, severity, disposition, test evidence, and canonical report hash; never copy credentials, raw provider responses, or unsafe metadata.

### Task 8: Verification, Live Evidence, Cleanup, and Runbook

**Files:**
- Create: `docs/runbooks/sprint-19-catalog-mappers.md`
- Modify: `plan/tasks/sprint-19/OPS-1901-catalog-mappers.md`
- Modify: `plan/sprints/sprint-19.md`
- Link: `../cps/docs/runbooks/sprint-19-catalog-contracts.md`

- [ ] **Step 1: Invoke verification discipline**

Invoke `superpowers:verification-before-completion`.

- [ ] **Step 2: Run fresh full quality gates**

Run:

```bash
rtk ruff check src tests
rtk mypy src
rtk pytest -q
python -m ops.contracts.validate_contracts
rtk pytest -q tests/contract/test_pin_against_cps.py
rtk git diff --check
detect-secrets scan --all-files
```

Expected: all commands exit 0; pin test proves exact CPS manifest identity; no new verified secret.

- [ ] **Step 3: Trigger inventory through CPS and poll terminal state**

Use the CPS commands from the paired plan with non-printed environment variables:

```bash
export CPS_URL="${CPS_URL:?set CPS_URL}"
export CPS_ADMIN_TOKEN="${CPS_ADMIN_TOKEN:?set CPS_ADMIN_TOKEN}"
export CONNECTION_ID="${CONNECTION_ID:?set CONNECTION_ID}"
export IDEMPOTENCY_KEY="ops-1901-$(date +%s)"

curl -fsS -X POST \
  -H "Authorization: Bearer $CPS_ADMIN_TOKEN" \
  -H "Idempotency-Key: $IDEMPOTENCY_KEY" \
  -H "Content-Type: application/json" \
  "$CPS_URL/api/v1/admin/provider-connections/$CONNECTION_ID/inventory-syncs" \
  -d '{"collections":["image","flavor"],"batch_size":100}' \
  > /tmp/ops-1901-sync.json

export OPERATION_ID="$(
  python -c 'import json; print(json.load(open("/tmp/ops-1901-sync.json"))["data"]["operation_id"])'
)"

for attempt in $(seq 1 60); do
  curl -fsS \
    -H "Authorization: Bearer $CPS_ADMIN_TOKEN" \
    "$CPS_URL/api/v1/admin/operations/$OPERATION_ID" \
    > /tmp/ops-1901-operation.json
  state="$(
    python -c 'import json; print(json.load(open("/tmp/ops-1901-operation.json"))["data"]["state"])'
  )"
  case "$state" in
    SUCCEEDED) break ;;
    FAILED|TIMED_OUT|CANCELLED) exit 1 ;;
  esac
  sleep 5
done
test "$state" = SUCCEEDED
```

Expected: terminal `SUCCEEDED`; OPS logs contain correlation/operation IDs but no credential, token, URL signature, raw body, or metadata value.

- [ ] **Step 4: Query CPS mapped inventory**

Run:

```bash
curl -fsS -H "Authorization: Bearer $CPS_ADMIN_TOKEN" \
  "$CPS_URL/api/v1/admin/provider-connections/$CONNECTION_ID/catalog?resource_type=image&limit=100" \
  > /tmp/ops-1901-cps-images.json
curl -fsS -H "Authorization: Bearer $CPS_ADMIN_TOKEN" \
  "$CPS_URL/api/v1/admin/provider-connections/$CONNECTION_ID/catalog?resource_type=flavor&limit=100" \
  > /tmp/ops-1901-cps-flavors.json
```

Expected: bounded enriched fields, stable IDs/order, approval marker, and no raw provider attributes.

- [ ] **Step 5: Independently verify provider fields and capabilities**

Run with an existing secure OpenStack CLI configuration:

```bash
openstack image list --long -f json > /tmp/ops-1901-os-images.json
openstack flavor list --all -f json > /tmp/ops-1901-os-flavors.json
openstack image show "$IMAGE_PROVIDER_ID" -f json > /tmp/ops-1901-os-image.json
openstack flavor show "$FLAVOR_PROVIDER_ID" -f json > /tmp/ops-1901-os-flavor.json
openstack flavor extra spec list "$FLAVOR_PROVIDER_ID" -f json \
  > /tmp/ops-1901-os-flavor-specs.json
openstack flavor access list "$FLAVOR_PROVIDER_ID" -f json \
  > /tmp/ops-1901-os-flavor-access.json
```

Compare IDs and every mapped material field. Query the CPS persisted capability document and compare service/API support with safe CLI observations. Record unsupported commands as capability limitations, not false support.

- [ ] **Step 6: Verify failure safety live**

Run a targeted refresh for a synthetically nonexistent provider ID and verify only direct 404 creates a tombstone. Simulate/induce one bounded timeout in a disposable test environment and verify no successful empty flavor/image collection and no CPS deletion.

- [ ] **Step 7: Prove cleanup**

OPS-1901 performs reads only and creates no provider resource. Record `none required`, compare pre/post image/flavor ID sets, and verify no disposable resource or local cache exists. Remove `/tmp/ops-1901-*.json` after extracting redacted comparisons.

- [ ] **Step 8: Write redacted runbook**

Create `docs/runbooks/sprint-19-catalog-mappers.md` with build/environment identifiers, exact commands and exit codes, CPS/OPS contract hashes, SDK APIs and call counts, capability reasons, operation/correlation IDs, redacted field comparison, failure-safety evidence, secret-scan disposition, cleanup proof, and limitations. Exclude credentials, tokens, `clouds.yaml`, signed URLs, binary data, `user_data`, raw service catalogs, raw provider bodies, and unsafe metadata.

- [ ] **Step 9: Link and update Sprint evidence**

Link the OPS runbook from the paired CPS runbook. Update OPS task/Sprint evidence only after deterministic and live gates pass. CPS-1901 must not be marked complete until the OPS pin hash and live mapper evidence are linked.

### Task 9: Finish Branch and Git Authorization Gate

**Files:** Entire reviewed OPS-1901 diff only.

- [ ] **Step 1: Invoke branch-finishing workflow**

Invoke `superpowers:finishing-a-development-branch`.

- [ ] **Step 2: Verify final scope**

Run:

```bash
rtk git status --short
rtk git diff --check
rtk git diff --stat
```

Expected: only files listed in this plan; no `clouds.yaml`, `.env`, credential, token, private key, cache, local data, captured provider body, or unrelated change.

- [ ] **Step 3: Stop for exact Git authorization**

Present the proposed commits and stop. Do not run `git add`, `git commit`, `git push`, amend, rebase, merge, or tag unless the user explicitly authorizes that exact action in the current turn.

- [ ] **Step 4: Execute only authorized boundaries**

If explicitly authorized, use Task 2–5 boundaries plus one evidence commit when useful; otherwise propose:

```text
feat(ops): enrich catalog mappers and capabilities
```

- [ ] **Step 5: Record Git evidence**

After an explicitly authorized push, record branch, commit hash(es), remote ref, and clean status in the OPS runbook and Sprint evidence. Do not claim Done before the paired CPS runbook links the final pin/live evidence.

## Plan Self-Review

- [x] Acceptance, out-of-scope, exact files, dependencies, and commit boundaries are explicit.
- [x] CPS canonical-first pin order, version behavior, fixtures, schemas, and checksum identity are explicit.
- [x] SDK field mapping, supported APIs, call budget, timeout, partial failure, targeted 404, and deterministic ordering decisions are exact.
- [x] RED, observed failure, minimal GREEN, and refactor steps cover contracts, mappers, collectors, and capabilities.
- [x] Independent luna review, remediation, and final re-review are mandatory.
- [x] Authorization/threat scope, secret/value sanitization, independent review, and task-diff secret scan are explicit.
- [x] Fresh focused/full gates, CPS-triggered terminal operation, OpenStack CLI comparison, failure safety, cleanup proof, and redacted runbook are exact.
- [x] Git mutation is separately authorization-gated and task-scoped.
- [x] No unresolved placeholder or design choice remains.
