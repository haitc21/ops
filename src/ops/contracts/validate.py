"""Contract tree validation and manifest generation for OPS."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ops.contracts.manifest import (
    compare_manifest_files,
    parse_manifest_files,
    validate_manifest_paths,
)
from ops.contracts.semantic import validate_contract_semantics

CONTRACTS_ROOT = Path(__file__).resolve().parent
MANIFEST_NAME = "checksums.json"


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    fixture_count: int
    message: str = ""


def _contract_files(base: Path) -> list[Path]:
    files: list[Path] = []
    for directory in ("fixtures", "jsonschema"):
        root = base / directory
        if root.exists():
            files.extend(
                path for path in root.rglob("*") if path.is_file() and path.name != ".gitkeep"
            )
    return sorted(files)


def compute_contract_checksums(base: Path) -> dict[str, str]:
    return {
        path.relative_to(base).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in _contract_files(base)
    }


def validate_contract_tree(root: Path | None = None) -> ValidationResult:
    base = root or CONTRACTS_ROOT
    computed = compute_contract_checksums(base)
    manifest_path = base / MANIFEST_NAME
    if not manifest_path.exists():
        return ValidationResult(
            ok=False,
            fixture_count=len(computed),
            message=f"missing {MANIFEST_NAME}",
        )

    manifest_files, parse_error = parse_manifest_files(manifest_path)
    if parse_error is not None:
        return ValidationResult(False, len(computed), parse_error)

    assert manifest_files is not None
    path_error = validate_manifest_paths(manifest_files)
    if path_error is not None:
        return ValidationResult(False, len(computed), path_error)

    compare_error = compare_manifest_files(manifest_files, computed)
    if compare_error is not None:
        return ValidationResult(False, len(computed), compare_error)

    fixture_count, semantic_error = validate_contract_semantics(base)
    if semantic_error is not None:
        return ValidationResult(False, fixture_count, semantic_error)
    return ValidationResult(ok=True, fixture_count=fixture_count, message="")


def write_contract_manifest(root: Path | None = None) -> ValidationResult:
    base = root or CONTRACTS_ROOT
    (base / "fixtures").mkdir(parents=True, exist_ok=True)
    (base / "jsonschema").mkdir(parents=True, exist_ok=True)
    computed = compute_contract_checksums(base)
    manifest_path = base / MANIFEST_NAME
    manifest_path.write_text(
        json.dumps({"files": computed}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return validate_contract_tree(base)


def assert_matches_cps_canonical(
    pinned_manifest: Path,
    *,
    ops_root: Path,
) -> None:
    local_manifest = ops_root / MANIFEST_NAME
    if not pinned_manifest.is_file():
        raise AssertionError(f"missing pinned CPS manifest: {pinned_manifest}")
    if not local_manifest.is_file():
        raise AssertionError(f"missing OPS manifest: {local_manifest}")
    if local_manifest.read_bytes() != pinned_manifest.read_bytes():
        raise AssertionError("OPS manifest differs from pinned CPS manifest")
    result = validate_contract_tree(ops_root)
    if not result.ok:
        raise AssertionError(result.message)


def main() -> None:
    result = validate_contract_tree()
    if not result.ok:
        raise SystemExit(f"contract validation failed: {result.message}")
    print(f"contracts ok ({result.fixture_count} files)")


if __name__ == "__main__":
    main()
