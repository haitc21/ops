"""Tests for safe contract manifest parsing and path rules."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ops.contracts.manifest import (
    normalize_manifest_path,
    parse_manifest_files,
    validate_manifest_paths,
    validate_manifest_root,
)
from ops.contracts.validate import validate_contract_tree


def test_validate_manifest_root_rejects_non_object() -> None:
    error = validate_manifest_root([])
    assert error == "manifest root must be an object"


def test_validate_manifest_root_rejects_unsupported_keys() -> None:
    error = validate_manifest_root({"files": {}, "extra": 1})
    assert error == "manifest contains unsupported keys"


def test_validate_manifest_root_rejects_non_mapping_files() -> None:
    error = validate_manifest_root({"files": []})
    assert error == "manifest files must be a mapping"


def test_parse_manifest_files_rejects_invalid_json(tmp_path: Path) -> None:
    manifest = tmp_path / "checksums.json"
    manifest.write_text("{bad", encoding="utf-8")
    files, error = parse_manifest_files(manifest)
    assert files is None
    assert error == "invalid checksums.json"


def test_parse_manifest_files_rejects_invalid_utf8(tmp_path: Path) -> None:
    manifest = tmp_path / "checksums.json"
    manifest.write_bytes(b"\xff\xfe")
    files, error = parse_manifest_files(manifest)
    assert files is None
    assert error == "invalid checksums.json"


def test_parse_manifest_files_reports_io_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "checksums.json"
    manifest.write_text("{}", encoding="utf-8")

    def fail_read(_self: Path, **_kwargs: object) -> str:
        raise OSError("synthetic inaccessible manifest")

    monkeypatch.setattr(Path, "read_text", fail_read)
    files, error = parse_manifest_files(manifest)
    assert files is None
    assert error == "unable to read checksums.json"


def test_parse_manifest_files_rejects_malformed_structure(tmp_path: Path) -> None:
    manifest = tmp_path / "checksums.json"
    manifest.write_text(json.dumps({"files": "not-a-map"}) + "\n", encoding="utf-8")
    files, error = parse_manifest_files(manifest)
    assert files is None
    assert error == "manifest files must be a mapping"


@pytest.mark.parametrize(
    ("path", "expected"),
    (
        ("", "manifest path must not be empty"),
        ("/absolute.json", "invalid manifest path: /absolute.json"),
        ("fixtures/../secret.json", "invalid manifest path: fixtures/../secret.json"),
        ("C:/drive.json", "invalid manifest path: C:/drive.json"),
        ("outside/tree.json", "invalid manifest path: outside/tree.json"),
        ("fixtures\\bad.json", "invalid manifest path: fixtures\\bad.json"),
    ),
)
def test_validate_manifest_paths_rejects_unsafe_paths(path: str, expected: str) -> None:
    error = validate_manifest_paths({path: "a" * 64})
    assert error == expected


def test_validate_manifest_paths_rejects_invalid_checksum() -> None:
    error = validate_manifest_paths({"fixtures/a.json": "NOTHEX"})
    assert error == "invalid manifest checksum: fixtures/a.json"


def test_validate_manifest_paths_rejects_duplicate_normalized_paths() -> None:
    error = validate_manifest_paths(
        {
            "fixtures/a.json": "a" * 64,
            "fixtures//a.json": "b" * 64,
        }
    )
    assert error == "duplicate manifest path: fixtures/a.json"


def test_normalize_manifest_path_collapses_slashes() -> None:
    assert normalize_manifest_path("fixtures//a.json") == "fixtures/a.json"


def test_validate_contract_tree_reports_missing_entry(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "only.json").write_text("{}", encoding="utf-8")
    (tmp_path / "checksums.json").write_text(
        json.dumps({"files": {}}) + "\n",
        encoding="utf-8",
    )
    result = validate_contract_tree(tmp_path)
    assert result.ok is False
    assert result.message == "missing manifest entry: fixtures/only.json"


def test_malformed_manifest_does_not_raise_attribute_error(tmp_path: Path) -> None:
    (tmp_path / "checksums.json").write_text('{"files": null}', encoding="utf-8")
    result = validate_contract_tree(tmp_path)
    assert result.ok is False
    assert result.message == "manifest files must be a mapping"


def test_validate_contract_tree_reports_extra_entry(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "only.json").write_text("{}", encoding="utf-8")
    digest = "a" * 64
    (tmp_path / "checksums.json").write_text(
        json.dumps(
            {
                "files": {
                    "fixtures/only.json": digest,
                    "fixtures/missing-on-disk.json": digest,
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = validate_contract_tree(tmp_path)
    assert result.ok is False
    assert result.message == "extra manifest entry: fixtures/missing-on-disk.json"
