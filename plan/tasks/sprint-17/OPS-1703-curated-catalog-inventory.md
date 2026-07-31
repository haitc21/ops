# OPS-1703 — Curated catalog inventory

**Status:** Done
**Active backlog:** No
**Paired task:** CPS-1703

## Outcome

OPS discovers image, flavor, network, availability-zone, and volume-type
catalog resources and emits a provider-neutral approval marker.

## Implementation

- Collect Cinder volume types and map approval from
  `extra_specs.cmp-catalog-approved`.
- Collect Nova availability zones and map approval from host-aggregate
  metadata because availability-zone records have no metadata field.
- Support targeted refresh for both resource types.
- Preserve stable ordering and fail closed when no approval marker exists.

## Evidence

- OPS full suite: 450 passed, 24 skipped; Ruff and MyPy pass.
- Live OpenStack collection returned approved AZ `nova` and volume type
  `__DEFAULT__` through operation
  `019fb676-18d7-7825-b8a9-5a75f55c25d8`.
