"""Regression matrix for Husky staged/worktree sync and NUL-safe secrets."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / ".husky" / "assert_staged_worktree_sync.py"
SECRETS_RUNNER = ROOT / ".husky" / "run_detect_secrets_on_staged.py"


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "seed")
    return repo


def _run_guard(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GUARD)],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def _try_symlink(link: Path, target: str) -> None:
    try:
        link.symlink_to(target)
    except OSError as exc:  # noqa: BLE001 - platform capability probe
        pytest.skip(f"symlinks unavailable: {exc}")


def test_add_synced_passes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    target = repo / "tracked.py"
    target.write_text("ok = True\n", encoding="utf-8")
    _git(repo, "add", "tracked.py")
    result = _run_guard(repo)
    assert result.returncode == 0, result.stderr


def test_add_with_unstaged_edit_fails(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    target = repo / "tracked.py"
    target.write_text("ok = True\n", encoding="utf-8")
    _git(repo, "add", "tracked.py")
    target.write_text("ok = False\n", encoding="utf-8")
    result = _run_guard(repo)
    assert result.returncode == 1
    assert "Staged file has unstaged changes: tracked.py" in result.stderr


def test_modify_synced_passes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    target = repo / "tracked.py"
    target.write_text("ok = True\n", encoding="utf-8")
    _git(repo, "add", "tracked.py")
    _git(repo, "commit", "-m", "add")
    target.write_text("ok = False\n", encoding="utf-8")
    _git(repo, "add", "tracked.py")
    result = _run_guard(repo)
    assert result.returncode == 0, result.stderr


def test_modify_with_unstaged_edit_fails(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    target = repo / "tracked.py"
    target.write_text("ok = True\n", encoding="utf-8")
    _git(repo, "add", "tracked.py")
    _git(repo, "commit", "-m", "add")
    target.write_text("staged\n", encoding="utf-8")
    _git(repo, "add", "tracked.py")
    target.write_text("unstaged\n", encoding="utf-8")
    result = _run_guard(repo)
    assert result.returncode == 1
    assert "Staged file has unstaged changes: tracked.py" in result.stderr


def test_delete_absent_passes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    target = repo / "doomed.py"
    target.write_text("payload\n", encoding="utf-8")
    _git(repo, "add", "doomed.py")
    _git(repo, "commit", "-m", "add")
    _git(repo, "rm", "doomed.py")
    result = _run_guard(repo)
    assert result.returncode == 0, result.stderr
    assert not target.exists()


def test_delete_restored_regular_file_fails(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    target = repo / "doomed.py"
    target.write_text("payload\n", encoding="utf-8")
    _git(repo, "add", "doomed.py")
    _git(repo, "commit", "-m", "add")
    _git(repo, "rm", "doomed.py")
    target.write_text("restored\n", encoding="utf-8")
    result = _run_guard(repo)
    assert result.returncode == 1
    assert "Staged file has unstaged changes: doomed.py" in result.stderr


def test_delete_restored_dangling_symlink_fails(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    target = repo / "doomed.py"
    target.write_text("payload\n", encoding="utf-8")
    _git(repo, "add", "doomed.py")
    _git(repo, "commit", "-m", "add")
    _git(repo, "rm", "doomed.py")
    _try_symlink(target, "missing-target")
    assert os.path.lexists(target)
    result = _run_guard(repo)
    assert result.returncode == 1
    assert "Staged file has unstaged changes: doomed.py" in result.stderr


def test_rename_normal_passes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    source = repo / "old_name.py"
    source.write_text("payload\n", encoding="utf-8")
    _git(repo, "add", "old_name.py")
    _git(repo, "commit", "-m", "add")
    _git(repo, "mv", "old_name.py", "new_name.py")
    result = _run_guard(repo)
    assert result.returncode == 0, result.stderr


def test_rename_new_path_unstaged_edit_fails(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    source = repo / "old_name.py"
    source.write_text("payload\n", encoding="utf-8")
    _git(repo, "add", "old_name.py")
    _git(repo, "commit", "-m", "add")
    _git(repo, "mv", "old_name.py", "new_name.py")
    (repo / "new_name.py").write_text("edited\n", encoding="utf-8")
    result = _run_guard(repo)
    assert result.returncode == 1
    assert "Staged file has unstaged changes: new_name.py" in result.stderr


def test_rename_old_path_restored_fails(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    source = repo / "old_name.py"
    source.write_text("payload\n", encoding="utf-8")
    _git(repo, "add", "old_name.py")
    _git(repo, "commit", "-m", "add")
    _git(repo, "mv", "old_name.py", "new_name.py")
    source.write_text("restored\n", encoding="utf-8")
    result = _run_guard(repo)
    assert result.returncode == 1
    assert "Staged file has unstaged changes: old_name.py" in result.stderr


def test_rename_old_path_dangling_symlink_fails(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    source = repo / "old_name.py"
    source.write_text("payload\n", encoding="utf-8")
    _git(repo, "add", "old_name.py")
    _git(repo, "commit", "-m", "add")
    _git(repo, "mv", "old_name.py", "new_name.py")
    _try_symlink(source, "missing-target")
    result = _run_guard(repo)
    assert result.returncode == 1
    assert "Staged file has unstaged changes: old_name.py" in result.stderr


def test_copy_with_source_present_passes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    source = repo / "source.py"
    source.write_text("payload\n", encoding="utf-8")
    _git(repo, "add", "source.py")
    _git(repo, "commit", "-m", "add")
    shutil.copyfile(source, repo / "copy.py")
    _git(repo, "add", "copy.py")
    status = _git(repo, "diff", "--cached", "--name-status", "-C").stdout
    if not any(line.startswith("C") for line in status.splitlines()):
        pytest.skip("git copy detection unavailable in this environment")
    result = _run_guard(repo)
    assert result.returncode == 0, result.stderr
    assert source.exists()


def test_copy_destination_unstaged_edit_fails(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    source = repo / "source.py"
    source.write_text("payload\n", encoding="utf-8")
    _git(repo, "add", "source.py")
    _git(repo, "commit", "-m", "add")
    shutil.copyfile(source, repo / "copy.py")
    _git(repo, "add", "copy.py")
    status = _git(repo, "diff", "--cached", "--name-status", "-C").stdout
    if not any(line.startswith("C") for line in status.splitlines()):
        pytest.skip("git copy detection unavailable in this environment")
    (repo / "copy.py").write_text("edited\n", encoding="utf-8")
    result = _run_guard(repo)
    assert result.returncode == 1
    assert "Staged file has unstaged changes: copy.py" in result.stderr


def test_has_unstaged_tracked_diff_exit_0_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    guard = _load_module(GUARD, "guard_exit_0")

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(guard.subprocess, "run", fake_run)
    assert guard.has_unstaged_tracked_diff("tracked.py") is False


def test_has_unstaged_tracked_diff_exit_1_is_true(monkeypatch: pytest.MonkeyPatch) -> None:
    guard = _load_module(GUARD, "guard_exit_1")

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(cmd, 1, b"", b"")

    monkeypatch.setattr(guard.subprocess, "run", fake_run)
    assert guard.has_unstaged_tracked_diff("tracked.py") is True


def test_has_unstaged_tracked_diff_exit_128_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard = _load_module(GUARD, "guard_exit_128")

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(cmd, 128, b"", b"fatal: not a git repository")

    monkeypatch.setattr(guard.subprocess, "run", fake_run)
    with pytest.raises(guard.GitDiffOperationalError) as exc_info:
        guard.has_unstaged_tracked_diff("tracked.py")
    message = str(exc_info.value)
    assert "git diff --quiet -- tracked.py" in message
    assert "fatal: not a git repository" in message
    assert "Staged file has unstaged changes" not in message


def test_main_quiet_operational_error_exit_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    guard = _load_module(GUARD, "guard_main_quiet_op")

    def boom(_path: str) -> bool:
        raise guard.GitDiffOperationalError(
            "git diff --quiet -- boom.py failed with exit 128: fatal: boom"
        )

    monkeypatch.setattr(
        guard,
        "iter_staged_name_status",
        lambda *, diff_filter: [("M", ("boom.py",))],
    )
    monkeypatch.setattr(guard, "has_unstaged_tracked_diff", boom)
    assert guard.main() == 2
    err = capsys.readouterr().err
    assert "fatal: boom" in err
    assert "Staged file has unstaged changes" not in err


def test_main_list_operational_error_exit_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    guard = _load_module(GUARD, "guard_main_list_op")

    def boom(*, diff_filter: str) -> list[tuple[str, tuple[str, ...]]]:
        raise guard.GitDiffOperationalError(
            "git diff --cached --name-status -z --diff-filter=ACMDR "
            "failed with exit 128: fatal: list boom"
        )

    monkeypatch.setattr(guard, "iter_staged_name_status", boom)
    assert guard.main() == 2
    err = capsys.readouterr().err
    assert "fatal: list boom" in err
    assert "Staged file has unstaged changes" not in err


def test_secret_list_operational_error_exit_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _load_module(SECRETS_RUNNER, "secrets_list_op")

    def boom() -> list[str]:
        raise runner.GitDiffOperationalError(
            "git diff --cached --name-only -z --diff-filter=ACMR "
            "failed with exit 128: fatal: secrets list boom"
        )

    monkeypatch.setattr(runner, "staged_secret_scan_paths", boom)
    assert runner.main() == 2
    err = capsys.readouterr().err
    assert "fatal: secrets list boom" in err
    assert "Traceback" not in err


def _baseline_arg(cmd: list[str]) -> str:
    idx = cmd.index("--baseline")
    return cmd[idx + 1]


def _synthetic_baseline(tmp_path: Path) -> Path:
    baseline = tmp_path / ".secrets.baseline"
    baseline.write_text('{"version":"1.5.0","results":{"sample.py":[]}}\n', encoding="utf-8")
    return baseline


def _patch_runner_baseline(
    monkeypatch: pytest.MonkeyPatch, runner: ModuleType, baseline: Path
) -> bytes:
    original_bytes = baseline.read_bytes()
    monkeypatch.setattr(runner, "BASELINE_PATH", baseline)
    return original_bytes


REDACTION_CANARY_VALUE = "redaction-canary-fixture-value"


def _assert_operational_stderr(err: str, *, redaction_canary: str) -> None:
    assert "Traceback" not in err
    assert "Direct cause" not in err
    assert "During handling" not in err
    assert redaction_canary not in err
    assert "canary_payload" not in err


def test_staged_baseline_excluded_from_secret_scan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _load_module(SECRETS_RUNNER, "secrets_exclude_baseline")
    baseline = _synthetic_baseline(tmp_path)
    _patch_runner_baseline(monkeypatch, runner, baseline)
    recorded: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if cmd[:3] == ["git", "diff", "--cached"]:
            return subprocess.CompletedProcess(cmd, 0, b".secrets.baseline\0normal.py\0", b"")
        recorded.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    assert runner.main() == 0
    assert len(recorded) == 1
    assert recorded[0][-1] == "normal.py"


def test_detect_secrets_passes_temp_baseline_to_every_child(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _load_module(SECRETS_RUNNER, "secrets_temp_baseline")
    baseline = _synthetic_baseline(tmp_path)
    _patch_runner_baseline(monkeypatch, runner, baseline)
    recorded: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        recorded.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(runner, "staged_secret_scan_paths", lambda: ["a.py", "b.py"])
    assert runner.main() == 0
    assert len(recorded) == 2
    baseline_paths = [_baseline_arg(cmd) for cmd in recorded]
    assert baseline_paths[0] == baseline_paths[1]
    for baseline_path in baseline_paths:
        assert baseline_path != ".secrets.baseline"
        assert Path(baseline_path).resolve() != baseline.resolve()


def _sandboxed_tempdir_factory(sandbox_root: Path):
    from tempfile import TemporaryDirectory as RealTemporaryDirectory

    def factory(*args: object, **kwargs: object) -> RealTemporaryDirectory:
        kwargs = dict(kwargs)
        kwargs["dir"] = str(sandbox_root)
        return RealTemporaryDirectory(*args, **kwargs)

    return factory


def test_malicious_child_cannot_alter_committed_baseline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _load_module(SECRETS_RUNNER, "secrets_malicious")
    baseline = _synthetic_baseline(tmp_path)
    original_bytes = _patch_runner_baseline(monkeypatch, runner, baseline)
    sandbox_root = tmp_path.resolve()
    committed_baseline = (ROOT / ".secrets.baseline").resolve()

    monkeypatch.setattr(
        runner,
        "TemporaryDirectory",
        _sandboxed_tempdir_factory(sandbox_root),
    )

    def malicious_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        target = Path(_baseline_arg(cmd)).resolve()
        assert target.is_relative_to(sandbox_root)
        assert target != baseline.resolve()
        assert target != committed_baseline
        target.write_text('{"results": {"hacked": true}}', encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(runner.subprocess, "run", malicious_run)
    monkeypatch.setattr(runner, "staged_secret_scan_paths", lambda: ["file.py"])
    assert runner.main() == 0
    assert baseline.read_bytes() == original_bytes


def test_detect_secrets_runner_preserves_space_tab_newline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _load_module(SECRETS_RUNNER, "secrets_argv")
    _patch_runner_baseline(monkeypatch, runner, _synthetic_baseline(tmp_path))
    weird = "dir/a file\twith\nnewline.py"
    recorded: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        recorded.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(runner, "staged_secret_scan_paths", lambda: [weird, "normal.py"])
    assert runner.main() == 0
    assert recorded[0][-1] == weird
    assert recorded[1][-1] == "normal.py"


@pytest.mark.parametrize("child_exit", (1, 2, 3))
def test_detect_secrets_preserves_child_exit_code_and_stops_scan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    child_exit: int,
) -> None:
    runner = _load_module(SECRETS_RUNNER, f"secrets_exit_{child_exit}")
    _patch_runner_baseline(monkeypatch, runner, _synthetic_baseline(tmp_path))
    recorded: list[list[str]] = []
    call_count = 0

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        nonlocal call_count
        call_count += 1
        recorded.append(list(cmd))
        if call_count == 1:
            return subprocess.CompletedProcess(cmd, child_exit, b"", b"")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(
        runner,
        "staged_secret_scan_paths",
        lambda: ["first.py", "second.py", "third.py"],
    )
    assert runner.main() == child_exit
    assert len(recorded) == 1
    assert recorded[0][-1] == "first.py"


def test_missing_baseline_fails_operationally_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    runner = _load_module(SECRETS_RUNNER, "secrets_missing_baseline")
    missing = tmp_path / ".secrets.baseline"
    redaction_canary = REDACTION_CANARY_VALUE
    present = tmp_path / "present.baseline"
    present.write_text(f'{{"canary_payload":"{redaction_canary}"}}\n', encoding="utf-8")

    monkeypatch.setattr(runner, "BASELINE_PATH", missing)
    monkeypatch.setattr(runner, "staged_secret_scan_paths", lambda: ["file.py"])
    assert runner.main() == 2
    _assert_operational_stderr(capsys.readouterr().err, redaction_canary=redaction_canary)
    assert present.read_bytes() == f'{{"canary_payload":"{redaction_canary}"}}\n'.encode()


def test_unreadable_baseline_fails_operationally_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    runner = _load_module(SECRETS_RUNNER, "secrets_unreadable_baseline")
    unreadable = tmp_path / ".secrets.baseline"
    unreadable.mkdir()
    redaction_canary = REDACTION_CANARY_VALUE
    present = tmp_path / "present.baseline"
    present.write_text(f'{{"canary_payload":"{redaction_canary}"}}\n', encoding="utf-8")

    monkeypatch.setattr(runner, "BASELINE_PATH", unreadable)
    monkeypatch.setattr(runner, "staged_secret_scan_paths", lambda: ["file.py"])
    assert runner.main() == 2
    _assert_operational_stderr(capsys.readouterr().err, redaction_canary=redaction_canary)
    assert present.read_bytes() == f'{{"canary_payload":"{redaction_canary}"}}\n'.encode()


TEMP_OPERATIONAL_MARKER = "temporary baseline operation failed"


def _assert_temp_operational_stderr(err: str, *, redaction_canary: str) -> None:
    _assert_operational_stderr(err, redaction_canary=redaction_canary)
    assert TEMP_OPERATIONAL_MARKER in err
    assert "No space left" not in err
    assert "secrets.baseline" not in err


def _failing_tempdir_factory(*, fail_enter: bool = False, fail_exit: bool = False):
    from tempfile import TemporaryDirectory as RealTemporaryDirectory

    def factory(*args: object, **kwargs: object) -> object:
        if fail_enter:

            class EnterFailure:
                def __enter__(self) -> str:
                    raise OSError(28, "No space left on device")

                def __exit__(self, *_args: object) -> bool:
                    return False

            return EnterFailure()

        real = RealTemporaryDirectory(*args, **kwargs)

        class Wrapper:
            def __enter__(self) -> str:
                return real.__enter__()

            def __exit__(self, *exit_args: object) -> bool:
                real.__exit__(*exit_args)
                if fail_exit:
                    raise OSError(28, "No space left on device")
                return False

        return Wrapper()

    return factory


def _patch_runner_for_temp_tests(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    tmp_path: Path,
) -> str:
    baseline = _synthetic_baseline(tmp_path)
    redaction_canary = REDACTION_CANARY_VALUE
    baseline.write_text(
        f'{{"version":"1.5.0","canary_payload":"{redaction_canary}","results":{{}}}}\n',
        encoding="utf-8",
    )
    _patch_runner_baseline(monkeypatch, runner, baseline)
    monkeypatch.setattr(runner, "staged_secret_scan_paths", lambda: ["file.py"])
    return redaction_canary


def test_temp_directory_creation_failure_exit_2(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    runner = _load_module(SECRETS_RUNNER, "secrets_tempdir_create_fail")
    redaction_canary = _patch_runner_for_temp_tests(monkeypatch, runner, tmp_path)
    monkeypatch.setattr(
        runner,
        "TemporaryDirectory",
        _failing_tempdir_factory(fail_enter=True),
    )
    assert runner.main() == 2
    _assert_temp_operational_stderr(capsys.readouterr().err, redaction_canary=redaction_canary)


def test_temp_baseline_write_failure_exit_2(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    runner = _load_module(SECRETS_RUNNER, "secrets_temp_baseline_write_fail")
    redaction_canary = _patch_runner_for_temp_tests(monkeypatch, runner, tmp_path)
    original_write_bytes = Path.write_bytes

    def fail_temp_write(self: Path, data: bytes) -> int:
        if self.name == "secrets.baseline":
            raise OSError(28, "No space left on device")
        return original_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", fail_temp_write)
    assert runner.main() == 2
    _assert_temp_operational_stderr(capsys.readouterr().err, redaction_canary=redaction_canary)


def test_temp_directory_cleanup_failure_after_success_exit_2(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    runner = _load_module(SECRETS_RUNNER, "secrets_tempdir_cleanup_success")
    redaction_canary = _patch_runner_for_temp_tests(monkeypatch, runner, tmp_path)

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(
        runner,
        "TemporaryDirectory",
        _failing_tempdir_factory(fail_exit=True),
    )
    assert runner.main() == 2
    _assert_temp_operational_stderr(capsys.readouterr().err, redaction_canary=redaction_canary)


def test_temp_directory_cleanup_failure_preserves_child_exit_1(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    runner = _load_module(SECRETS_RUNNER, "secrets_tempdir_cleanup_child1")
    redaction_canary = _patch_runner_for_temp_tests(monkeypatch, runner, tmp_path)

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(cmd, 1, b"", b"")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(
        runner,
        "TemporaryDirectory",
        _failing_tempdir_factory(fail_exit=True),
    )
    assert runner.main() == 1
    _assert_temp_operational_stderr(capsys.readouterr().err, redaction_canary=redaction_canary)


def test_fsdecode_surrogateescape_roundtrip() -> None:
    guard = _load_module(GUARD, "guard_fsdecode")
    raw = b"file\xffname.py"
    paths = guard.split_nul_paths(raw + b"\0")
    assert len(paths) == 1
    assert (
        os.fsencode(paths[0]) == raw
        or paths[0].encode(sys.getfilesystemencoding(), errors="surrogateescape") == raw
    )


def test_nul_split_keeps_embedded_newlines() -> None:
    runner = _load_module(SECRETS_RUNNER, "secrets_nul_split")
    raw = b"file with space.py\0file\twith\ttab.py\0file\nwith\nnewline.py\0"
    assert runner.split_nul_paths(raw) == [
        "file with space.py",
        "file\twith\ttab.py",
        "file\nwith\nnewline.py",
    ]


def test_husky_pre_commit_guard_before_quality_gates_and_nul_secrets() -> None:
    hook = (ROOT / ".husky" / "pre-commit").read_text(encoding="utf-8")
    assert "assert_staged_worktree_sync" in hook
    assert "run_detect_secrets_on_staged" in hook
    assert "while IFS= read -r file" not in hook
    assert hook.find("assert_staged_worktree_sync") < hook.find("ruff format --check")
    assert hook.find("ruff format --check") < hook.find("run_detect_secrets_on_staged")


@pytest.mark.parametrize("child_exit", (1, 2))
def test_husky_pre_commit_preserves_guard_exit_code(tmp_path: Path, child_exit: int) -> None:
    shell = shutil.which("sh")
    if shell is None and (git := shutil.which("git")) is not None:
        git_bash = Path(git).resolve().parents[1] / "bin" / "sh.exe"
        if git_bash.is_file():
            shell = str(git_bash)
    if shell is None:
        pytest.skip("POSIX-compatible sh is required")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_uv = bin_dir / "uv"
    fake_uv.write_text(f"#!/bin/sh\nexit {child_exit}\n", encoding="utf-8", newline="\n")
    fake_uv.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [shell, str(ROOT / ".husky" / "pre-commit")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == child_exit
