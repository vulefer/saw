import subprocess
import sys

from scripts import run_release_tests


def test_release_command_runs_every_required_suite(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, *, cwd, check):
        captured.update(command=command, cwd=cwd, check=check)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(run_release_tests.subprocess, "run", fake_run)

    assert run_release_tests.main() == 0
    assert captured == {
        "command": [
            sys.executable,
            "-m",
            "pytest",
            "tests/unit",
            "tests/regression",
            "tests/oracle",
            "tests/test_minimal_example.py",
        ],
        "cwd": run_release_tests.REPOSITORY_ROOT,
        "check": False,
    }


def test_release_command_preserves_a_failing_test_exit_status(
    monkeypatch,
) -> None:
    def fake_run(command, *, cwd, check):
        return subprocess.CompletedProcess(command, 1)

    monkeypatch.setattr(run_release_tests.subprocess, "run", fake_run)

    assert run_release_tests.main() == 1
