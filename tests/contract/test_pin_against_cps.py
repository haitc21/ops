"""OPS pinned CPS contracts must match the immutable canonical snapshot."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ops.contracts.validate import assert_matches_cps_canonical

CATALOG_ARTIFACTS = (
    "jsonschema/inventory_batch.schema.json",
    "jsonschema/capability_document.schema.json",
    "fixtures/events/inventory_batch_image_full.json",
    "fixtures/events/inventory_batch_image_minimal.json",
    "fixtures/events/inventory_batch_flavor_full.json",
    "fixtures/events/inventory_batch_flavor_minimal.json",
)


def test_local_contract_tree_matches_pinned_cps_manifest() -> None:
    contracts = Path("src/ops/contracts")
    assert_matches_cps_canonical(
        contracts / "cps_checksums.pinned.json",
        ops_root=contracts,
    )


def test_catalog_artifacts_are_pinned_from_cps() -> None:
    contracts = Path("src/ops/contracts")
    configured = os.getenv("CPS_CONTRACTS_ROOT")
    cps = Path(configured) if configured else Path("../cps/src/cps/contracts")
    if not cps.is_dir():
        pinned = json.loads((contracts / "cps_checksums.pinned.json").read_text())
        local = json.loads((contracts / "checksums.json").read_text())
        for relative in CATALOG_ARTIFACTS:
            assert pinned["files"][relative] == local["files"][relative]
        return
    for relative in CATALOG_ARTIFACTS:
        assert (contracts / relative).read_bytes() == (cps / relative).read_bytes()


def test_safe_metadata_helper_preserves_cps_bounds() -> None:
    from ops.contracts import safe_metadata

    assert safe_metadata.MAX_ROOT_MAP_ENTRIES == 128
    assert safe_metadata.MAX_TREE_DEPTH == 4
    assert safe_metadata.MAX_ATTACHMENT_SERIALIZED_BYTES == 64 * 1024


def test_pin_fails_when_pinned_manifest_is_missing(tmp_path: Path) -> None:
    contracts = Path("src/ops/contracts")
    with pytest.raises(AssertionError, match="missing pinned CPS manifest"):
        assert_matches_cps_canonical(tmp_path / "missing.json", ops_root=contracts)


def test_pin_fails_when_manifest_bytes_drift(tmp_path: Path) -> None:
    contracts = Path("src/ops/contracts")
    pin = tmp_path / "pin.json"
    pin.write_text('{"files": {}}\n', encoding="utf-8")
    with pytest.raises(AssertionError, match="differs"):
        assert_matches_cps_canonical(pin, ops_root=contracts)
