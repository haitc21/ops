"""Contract validation must not auto-create missing manifests."""

from __future__ import annotations

import json
from pathlib import Path

from ops.contracts.validate import (
    ValidationResult,
    validate_contract_tree,
    write_contract_manifest,
)


def test_validate_fails_when_manifest_missing(tmp_path: Path) -> None:
    (tmp_path / "fixtures").mkdir()
    result = validate_contract_tree(tmp_path)
    assert result.ok is False
    assert "missing" in result.message.lower()
    assert not (tmp_path / "checksums.json").exists()


def test_write_manifest_creates_checksums_file(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "example.json").write_text("{}", encoding="utf-8")

    result = write_contract_manifest(tmp_path)
    assert result.ok is True
    manifest = json.loads((tmp_path / "checksums.json").read_text(encoding="utf-8"))
    assert "fixtures/example.json" in manifest["files"]

    validated = validate_contract_tree(tmp_path)
    assert validated == ValidationResult(ok=True, file_count=1, message="")


def test_manifest_detects_jsonschema_change(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    schemas = tmp_path / "jsonschema"
    fixtures.mkdir()
    schemas.mkdir()
    (fixtures / "message.json").write_text("{}", encoding="utf-8")
    schema = schemas / "message.schema.json"
    schema.write_text("{}", encoding="utf-8")

    write_contract_manifest(tmp_path)
    schema.write_text('{"type": "object"}', encoding="utf-8")

    result = validate_contract_tree(tmp_path)
    assert result.ok is False
    assert result.message == "contract checksum mismatch"


def test_manifest_uses_files_key(tmp_path: Path) -> None:
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "jsonschema").mkdir()
    write_contract_manifest(tmp_path)
    manifest = json.loads((tmp_path / "checksums.json").read_text(encoding="utf-8"))
    assert manifest == {"files": {}}


def test_validate_success_reports_manifest_managed_file_count(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    schemas = tmp_path / "jsonschema"
    fixtures.mkdir()
    schemas.mkdir()
    for index in range(6):
        (fixtures / f"item{index}.json").write_text("{}", encoding="utf-8")
    for index in range(2):
        (schemas / f"schema{index}.json").write_text("{}", encoding="utf-8")

    write_contract_manifest(tmp_path)
    result = validate_contract_tree(tmp_path)

    assert result.ok is True
    assert result.file_count == 8
