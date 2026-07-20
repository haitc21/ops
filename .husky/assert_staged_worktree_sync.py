#!/usr/bin/env python3
"""Fail when the working tree would not match the post-commit index tree.

Husky quality gates read working-tree files. Any divergence between the staged
index and the filesystem tree those gates observe must fail closed before
Ruff/mypy/pytest/contracts/detect-secrets run.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import NoReturn

OPERATIONAL_EXIT_CODE = 2
MISMATCH_EXIT_CODE = 1


class GitDiffOperationalError(RuntimeError):
    """Raised when a Git command fails with an operational exit code."""


def fsdecode_path(data: bytes) -> str:
    """Decode Git/NUL path bytes via ``os.fsdecode``, preserving opaque bytes."""
    try:
        return os.fsdecode(data)
    except UnicodeDecodeError:
        return data.decode(sys.getfilesystemencoding(), errors="surrogateescape")


def split_nul_paths(payload: bytes) -> list[str]:
    if not payload:
        return []
    return [fsdecode_path(part) for part in payload.split(b"\0") if part]


def _format_command(command: list[str]) -> str:
    return " ".join(command)


def _decode_git_stderr(payload: bytes) -> str:
    return payload.decode("utf-8", errors="surrogateescape").strip()


def _raise_operational(command: list[str], returncode: int, stderr: bytes) -> NoReturn:
    detail = f"{_format_command(command)} failed with exit {returncode}"
    text = _decode_git_stderr(stderr)
    if text:
        detail = f"{detail}: {text}"
    raise GitDiffOperationalError(detail)


def _git_bytes(*args: str) -> bytes:
    command = ["git", *args]
    completed = subprocess.run(command, check=False, capture_output=True)
    if completed.returncode != 0:
        _raise_operational(command, completed.returncode, completed.stderr)
    return completed.stdout


def iter_staged_name_status(*, diff_filter: str) -> list[tuple[str, tuple[str, ...]]]:
    """Parse ``git diff --cached --name-status -z`` entries."""
    raw = _git_bytes(
        "diff",
        "--cached",
        "--name-status",
        "-z",
        "-M",
        "-C",
        f"--diff-filter={diff_filter}",
    )
    tokens = split_nul_paths(raw)
    entries: list[tuple[str, tuple[str, ...]]] = []
    index = 0
    while index < len(tokens):
        status = tokens[index]
        index += 1
        if status.startswith(("R", "C")):
            if index + 1 >= len(tokens):
                raise GitDiffOperationalError(f"incomplete rename/copy status for {status!r}")
            old_path = tokens[index]
            new_path = tokens[index + 1]
            index += 2
            entries.append((status, (old_path, new_path)))
            continue
        if index >= len(tokens):
            raise GitDiffOperationalError(f"incomplete status for {status!r}")
        path = tokens[index]
        index += 1
        entries.append((status, (path,)))
    return entries


def has_unstaged_tracked_diff(path: str) -> bool:
    """Return whether ``path`` has an unstaged tracked diff.

    ``git diff --quiet`` exit codes:
    - 0: no unstaged diff
    - 1: unstaged diff present
    - >1: operational failure
    """
    command = ["git", "diff", "--quiet", "--", path]
    completed = subprocess.run(command, check=False, capture_output=True)
    if completed.returncode == 0:
        return False
    if completed.returncode == 1:
        return True
    _raise_operational(command, completed.returncode, completed.stderr)


def path_lexists(path: str) -> bool:
    """True if ``path`` exists in the working tree, including dangling symlinks."""
    return os.path.lexists(path)


def staged_paths_out_of_sync() -> list[str]:
    """Return staged ACMDR paths whose working tree does not match the index."""
    bad: list[str] = []
    for status, paths in iter_staged_name_status(diff_filter="ACMDR"):
        if status.startswith("D"):
            path = paths[0]
            if path_lexists(path):
                bad.append(path)
            continue
        if status.startswith("R"):
            old_path, new_path = paths
            if path_lexists(old_path):
                bad.append(old_path)
            if has_unstaged_tracked_diff(new_path):
                bad.append(new_path)
            continue
        if status.startswith("C"):
            _old_path, new_path = paths
            if has_unstaged_tracked_diff(new_path):
                bad.append(new_path)
            continue
        path = paths[0]
        if has_unstaged_tracked_diff(path):
            bad.append(path)
    return bad


def main() -> int:
    try:
        bad_paths = staged_paths_out_of_sync()
    except GitDiffOperationalError as exc:
        print(str(exc), file=sys.stderr)
        return OPERATIONAL_EXIT_CODE

    failed = False
    for path in bad_paths:
        print(f"Staged file has unstaged changes: {path}", file=sys.stderr)
        failed = True
    return MISMATCH_EXIT_CODE if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
