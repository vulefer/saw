from pathlib import Path
import sys

import pytest

from scripts import run_release_tests

ALL_PHASES = ["static", "build", "install", "tests", "schemas", "examples", "smoke"]


def test_by_default_every_phase_runs_in_ci_order() -> None:
    assert run_release_tests.select_phases([], [], current_environment=False) == ALL_PHASES


def test_test_phases_get_a_fresh_environment_unless_told_otherwise() -> None:
    assert run_release_tests.select_phases(["tests"], [], current_environment=False) == [
        "install",
        "tests",
    ]
    assert run_release_tests.select_phases(["tests"], [], current_environment=True) == ["tests"]
    with pytest.raises(SystemExit, match="need install"):
        run_release_tests.select_phases([], ["install"], current_environment=False)


def test_the_test_phases_cover_the_whole_test_tree() -> None:
    tests = Path(run_release_tests.REPOSITORY_ROOT) / "tests"
    expected = {f"tests/{path.name}" for path in tests.iterdir() if path.is_dir()}
    expected |= {f"tests/{path.name}" for path in tests.glob("test_*.py")}
    expected -= {"tests/__pycache__"}

    covered = {path for paths in run_release_tests.TEST_PHASES.values() for path in paths}

    assert covered == expected


def test_each_test_phase_runs_pytest_with_the_selected_interpreter(monkeypatch) -> None:
    commands = []
    monkeypatch.setattr(run_release_tests, "_call", lambda *command: commands.append(command) or 0)
    monkeypatch.setattr(run_release_tests, "saw_location", lambda python: "site-packages")

    assert run_release_tests.main(["--current-environment", "--only", "schemas"]) == 0
    assert commands == [(sys.executable, "-m", "pytest", "tests/contracts")]


def _fake_runners(monkeypatch, failing: set[str]) -> list[str]:
    ran: list[str] = []

    def runner(phase):
        return lambda run: ran.append(phase) or (1 if phase in failing else 0)

    monkeypatch.setattr(
        run_release_tests, "RUNNERS", {phase: runner(phase) for phase in ALL_PHASES}
    )
    return ran


def test_a_failed_phase_is_named_and_blocks_only_what_needs_it(monkeypatch, capsys) -> None:
    ran = _fake_runners(monkeypatch, failing={"build"})

    assert run_release_tests.main([]) == 1

    # static does not need the build, everything else does
    assert ran == ["static", "build"]
    output = capsys.readouterr()
    assert "install: not run, build failed" in output.out
    assert "smoke: not run, build failed" in output.out
    assert (
        "error: release gate failed: build, install, tests, schemas, examples, smoke" in output.err
    )


def test_a_failed_test_phase_does_not_stop_the_others(monkeypatch, capsys) -> None:
    ran = _fake_runners(monkeypatch, failing={"schemas"})

    assert run_release_tests.main([]) == 1

    assert ran == ALL_PHASES
    assert "error: release gate failed: schemas" in capsys.readouterr().err


def test_every_phase_passing_exits_zero(monkeypatch) -> None:
    _fake_runners(monkeypatch, failing=set())

    assert run_release_tests.main([]) == 0
