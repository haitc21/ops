#!/usr/bin/env python3
"""Run detect-secrets-hook on staged ACMR paths using NUL-safe path lists."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

OPERATIONAL_EXIT_CODE = 2
TEMP_BASELINE_OPERATIONAL_MESSAGE = "temporary baseline operation failed"
BASELINE_PATH = Path(".secrets.baseline")
BASELINE_STAGED_PATH = BASELINE_PATH.as_posix()


class GitDiffOperationalError(RuntimeError):
    """Raised when listing staged paths fails operationally."""


class BaselineOperationalError(RuntimeError):
    """Raised when the secrets baseline cannot be read."""


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
    baseline = BASELINE_STAGED_PATH
    return [path for path in split_nul_paths(completed.stdout) if path != baseline]


def _read_baseline_bytes() -> bytes:
    try:
        return BASELINE_PATH.read_bytes()
    except FileNotFoundError:
        raise BaselineOperationalError(f"baseline not found: {BASELINE_PATH}") from None
    except OSError:
        raise BaselineOperationalError(f"baseline unreadable: {BASELINE_PATH}") from None


def _detect_secrets_command(baseline_path: str, path: str) -> list[str]:
    if shutil.which("uv"):
        return [
            "uv",
            "run",
            "detect-secrets-hook",
            "--baseline",
            baseline_path,
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
            baseline_path,
            path,
        ]
    return [
        sys.executable,
        "-m",
        "uv",
        "run",
        "detect-secrets-hook",
        "--baseline",
        baseline_path,
        path,
    ]


def main() -> int:
    try:
        paths = staged_secret_scan_paths()
    except GitDiffOperationalError as exc:
        print(str(exc), file=sys.stderr)
        return OPERATIONAL_EXIT_CODE

    try:
        baseline_bytes = _read_baseline_bytes()
    except BaselineOperationalError as exc:
        print(str(exc), file=sys.stderr)
        return OPERATIONAL_EXIT_CODE

    child_exit: int | None = None
    try:
        with TemporaryDirectory() as tmpdir:
            temp_baseline = Path(tmpdir) / "secrets.baseline"
            temp_baseline.write_bytes(baseline_bytes)
            temp_baseline_str = str(temp_baseline)

            for path in paths:
                completed = subprocess.run(
                    _detect_secrets_command(temp_baseline_str, path),
                    check=False,
                    env=os.environ.copy(),
                )
                if completed.returncode != 0:
                    child_exit = completed.returncode
                    break
    except OSError:
        print(TEMP_BASELINE_OPERATIONAL_MESSAGE, file=sys.stderr)
        if child_exit is not None:
            return child_exit
        return OPERATIONAL_EXIT_CODE

    if child_exit is not None:
        return child_exit
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
