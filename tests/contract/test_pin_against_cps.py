"""OPS pinned CPS contracts must match the immutable canonical snapshot."""

from __future__ import annotations

from pathlib import Path

import pytest

from ops.contracts.validate import assert_matches_cps_canonical


def test_local_contract_tree_matches_pinned_cps_manifest() -> None:
    contracts = Path("src/ops/contracts")
    assert_matches_cps_canonical(
        contracts / "cps_checksums.pinned.json",
        ops_root=contracts,
    )


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
