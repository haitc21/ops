"""OPS pinned CPS contracts must match the immutable canonical snapshot."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ops.contracts.messages.inventory import InventoryBatchPayload
from ops.contracts.validate import assert_matches_cps_canonical

_CATALOG_CONTRACT_FILES = frozenset(
    {
        "fixtures/events/inventory_batch_flavor_full.json",
        "fixtures/events/inventory_batch_flavor_minimal.json",
        "fixtures/events/inventory_batch_image_full.json",
        "fixtures/events/inventory_batch_image_minimal.json",
        "jsonschema/inventory_batch.schema.json",
    }
)


def test_local_contract_tree_matches_pinned_cps_manifest() -> None:
    contracts = Path("src/ops/contracts")
    assert_matches_cps_canonical(
        contracts / "cps_checksums.pinned.json",
        ops_root=contracts,
    )


def test_cps_1901_catalog_contract_files_are_pinned_and_semantically_valid() -> None:
    contracts = Path("src/ops/contracts")
    for relative_path in _CATALOG_CONTRACT_FILES:
        assert (contracts / relative_path).is_file(), relative_path

    payload = (contracts / "fixtures/events/inventory_batch_image_full.json").read_text(
        encoding="utf-8"
    )
    InventoryBatchPayload.model_validate(json.loads(payload)["payload"])


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
