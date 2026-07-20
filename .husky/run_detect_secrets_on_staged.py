#!/usr/bin/env python3
"""Run detect-secrets-hook on staged ACMR paths using NUL-safe path lists."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

OPERATIONAL_EXIT_CODE = 2


class GitDiffOperationalError(RuntimeError):
    """Raised when listing staged paths fails operationally."""


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


def staged_secret_scan_paths() -> list[str]:
    command = ["git", "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR"]
    completed = subprocess.run(command, check=False, capture_output=True)
    if completed.returncode != 0:
        detail = f"{_format_command(command)} failed with exit {completed.returncode}"
        text = _decode_git_stderr(completed.stderr)
        if text:
            detail = f"{detail}: {text}"
        raise GitDiffOperationalError(detail)
    return split_nul_paths(completed.stdout)


def _detect_secrets_command(path: str) -> list[str]:
    if shutil.which("uv"):
        return [
            "uv",
            "run",
            "detect-secrets-hook",
            "--baseline",
            ".secrets.baseline",
            path,
        ]
    if shutil.which("py"):
        return [
            "py",
            "-3.12",
            "-m",
            "uv",
            "run",
            "detect-secrets-hook",
            "--baseline",
            ".secrets.baseline",
            path,
        ]
    return [
        sys.executable,
        "-m",
        "uv",
        "run",
        "detect-secrets-hook",
        "--baseline",
        ".secrets.baseline",
        path,
    ]


def main() -> int:
    try:
        paths = staged_secret_scan_paths()
    except GitDiffOperationalError as exc:
        print(str(exc), file=sys.stderr)
        return OPERATIONAL_EXIT_CODE

    for path in paths:
        completed = subprocess.run(
            _detect_secrets_command(path),
            check=False,
            env=os.environ.copy(),
        )
        if completed.returncode != 0:
            return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
