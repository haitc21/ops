"""Safe parsing and validation for contract checksum manifests."""

from __future__ import annotations

import json
import re
from pathlib import Path

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_PREFIXES = ("fixtures/", "jsonschema/")


def normalize_manifest_path(path: str) -> str:
    parts = [part for part in path.split("/") if part and part != "."]
    return "/".join(parts)


def validate_manifest_root(manifest: object) -> str | None:
    if not isinstance(manifest, dict):
        return "manifest root must be an object"
    if set(manifest.keys()) != {"files"}:
        return "manifest contains unsupported keys"
    if not isinstance(manifest["files"], dict):
        return "manifest files must be a mapping"
    return None


def parse_manifest_files(manifest_path: Path) -> tuple[dict[str, str] | None, str | None]:
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError:
        return None, "unable to read checksums.json"
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "invalid checksums.json"
    error = validate_manifest_root(raw)
    if error is not None:
        return None, error
    files = raw["files"]
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in files.items()):
        return None, "manifest files must be a mapping"
    return files, None


def validate_manifest_entry_path(path: str) -> str | None:
    if not path:
        return "manifest path must not be empty"
    if path.startswith("/") or "\\" in path:
        return f"invalid manifest path: {path}"
    if len(path) >= 2 and path[1] == ":":
        return f"invalid manifest path: {path}"
    if ".." in path.split("/"):
        return f"invalid manifest path: {path}"
    if not any(path.startswith(prefix) for prefix in _ALLOWED_PREFIXES):
        return f"invalid manifest path: {path}"
    return None


def validate_manifest_paths(files: dict[str, str]) -> str | None:
    seen: dict[str, str] = {}
    for path, checksum in files.items():
        path_error = validate_manifest_entry_path(path)
        if path_error is not None:
            return path_error
        if not _SHA256_RE.fullmatch(checksum):
            return f"invalid manifest checksum: {path}"
        normalized = normalize_manifest_path(path)
        previous = seen.get(normalized)
        if previous is not None and previous != path:
            return f"duplicate manifest path: {normalized}"
        seen[normalized] = path
    return None


def compare_manifest_files(
    manifest_files: dict[str, str],
    computed_files: dict[str, str],
) -> str | None:
    for path in sorted(computed_files.keys() - manifest_files.keys()):
        return f"missing manifest entry: {path}"
    for path in sorted(manifest_files.keys() - computed_files.keys()):
        return f"extra manifest entry: {path}"
    for path, expected in computed_files.items():
        if manifest_files[path] != expected:
            return "contract checksum mismatch"
    return None
