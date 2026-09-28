import subprocess
import sys

from scripts import run_static_checks


def _record_runs(monkeypatch, failing: set[str] = frozenset()) -> list[list[str]]:
    commands: list[list[str]] = []

    def fake_run(command, *, cwd, check):
        assert cwd == run_static_checks.REPOSITORY_ROOT
        assert check is False
        commands.append(command)
        return subprocess.CompletedProcess(command, 1 if command[2] in failing else 0)

    monkeypatch.setattr(run_static_checks.subprocess, "run", fake_run)
    return commands


def test_every_check_runs_through_the_current_interpreter(monkeypatch) -> None:
    commands = _record_runs(monkeypatch)

    assert run_static_checks.main([]) == 0
    assert commands == [
        [sys.executable, "-m", "ruff", "format", "--check", "--diff"],
        [sys.executable, "-m", "ruff", "check"],
        [sys.executable, "-m", "mypy"],
    ]


def test_fix_rewrites_instead_of_checking(monkeypatch) -> None:
    commands = _record_runs(monkeypatch)

    assert run_static_checks.main(["--fix"]) == 0
    assert commands[:2] == [
        [sys.executable, "-m", "ruff", "format"],
        [sys.executable, "-m", "ruff", "check", "--fix"],
    ]


def test_a_failed_check_is_named_and_the_rest_still_run(monkeypatch, capsys) -> None:
    commands = _record_runs(monkeypatch, failing={"mypy"})

    assert run_static_checks.main([]) == 1
    assert len(commands) == 3
    assert "static checks failed: types" in capsys.readouterr().err
