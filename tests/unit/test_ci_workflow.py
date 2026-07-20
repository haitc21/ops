"""OPS-004: local quality-gate acceptance checks."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_husky_pre_commit_defines_required_quality_gates() -> None:
    hook = (ROOT / ".husky" / "pre-commit").read_text(encoding="utf-8")
    required = [
        "assert_staged_worktree_sync",
        "ruff format --check",
        "ruff check",
        "mypy",
        "pytest",
        "run_detect_secrets_on_staged",
        "validate_contracts",
        "assert_matches_cps_canonical",
    ]
    missing = [item for item in required if item not in hook]
    assert missing == [], f"Husky hook missing gates: {missing}"


def test_github_actions_workflows_are_absent() -> None:
    workflows = ROOT / ".github" / "workflows"
    assert not workflows.exists() or not any(workflows.iterdir())
