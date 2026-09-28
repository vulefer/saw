"""Run every gate required for a Saw release, in the order CI runs them."""

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import venv

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DIST_DIRECTORY = REPOSITORY_ROOT / "dist"
DEV_LOCK = REPOSITORY_ROOT / "requirements" / "dev.txt"

# pytest paths of each test phase, together they are the whole release suite
TEST_PHASES = {
    "tests": (
        "tests/unit",
        "tests/integration",
        "tests/regression",
        "tests/oracle",
        "tests/compatibility",
    ),
    "schemas": ("tests/contracts",),
    "examples": ("tests/test_minimal_example.py", "tests/test_documentation_examples.py"),
}
# in run order; install creates the fresh environment the test phases use
PHASES = ("static", "build", "install", *TEST_PHASES, "smoke")
# a phase is not run when a phase it needs failed earlier in the same run
PREREQUISITES = {
    "install": ("build",),
    **{phase: ("install",) for phase in TEST_PHASES},
    "smoke": ("build",),
}
# interpreter settings that would let the fresh environment see the checkout
# or another site-packages
LEAKING_VARIABLES = ("PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "VIRTUAL_ENV")


@dataclass
class Run:
    """The state the phases of one invocation share."""

    # the interpreter the test phases run with, replaced by install
    python: str
    allow_dirty: bool
    directory: Path


def _environment_variables() -> dict[str, str]:
    variables = {k: v for k, v in os.environ.items() if k not in LEAKING_VARIABLES}
    variables["PYTHONNOUSERSITE"] = "1"
    return variables


def _call(*command: object) -> int:
    completed = subprocess.run(
        [str(part) for part in command],
        cwd=REPOSITORY_ROOT,
        env=_environment_variables(),
        check=False,
    )
    return completed.returncode


def _static(run: Run) -> int:
    return _call(sys.executable, "scripts/run_static_checks.py")


def _build(run: Run) -> int:
    dirty = ["--allow-dirty"] if run.allow_dirty else []
    return _call(sys.executable, "scripts/build_distributions.py", *dirty)


def _install(run: Run) -> int:
    wheels = sorted(DIST_DIRECTORY.glob("saw-*.whl"))
    if len(wheels) != 1:
        print(f"error: expected one saw wheel in dist/, found {len(wheels)}", file=sys.stderr)
        return 1
    root = run.directory / "venv"
    print(f"installing {wheels[0].name} into a fresh environment in {root}", flush=True)
    venv.EnvBuilder(with_pip=True, clear=True).create(root)
    scripts = root / ("Scripts" if os.name == "nt" else "bin")
    run.python = str(scripts / ("python.exe" if os.name == "nt" else "python"))
    pip = (run.python, "-m", "pip", "--disable-pip-version-check", "--quiet")
    return _call(*pip, "install", "--constraint", DEV_LOCK, f"{wheels[0]}[dev]")


def _smoke(run: Run) -> int:
    return _call(sys.executable, "scripts/smoke_test_wheel.py")


def _test_phase(paths: Sequence[str]) -> Callable[[Run], int]:
    # python -m pytest puts the checkout root on sys.path for tests/ and
    # scripts/; saw comes from the environment, src/ is never on sys.path
    return lambda run: _call(run.python, "-m", "pytest", *paths)


RUNNERS = {
    "static": _static,
    "build": _build,
    "install": _install,
    **{phase: _test_phase(paths) for phase, paths in TEST_PHASES.items()},
    "smoke": _smoke,
}


def saw_location(python: str) -> str:
    """Return where *python* imports saw from."""
    completed = subprocess.run(
        [python, "-c", "import saw; print(saw.__file__)"],
        cwd=REPOSITORY_ROOT,
        env=_environment_variables(),
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "nowhere, it does not import"


def select_phases(only: Sequence[str], skip: Sequence[str], current_environment: bool) -> list[str]:
    """Return the phases to run, in order."""
    selected = [phase for phase in PHASES if (not only or phase in only) and phase not in skip]
    if current_environment:
        return [phase for phase in selected if phase != "install"]
    # without --current-environment the test phases always run in a fresh
    # environment built from the wheel, never in the developer's one
    first_test = next((index for index, phase in enumerate(selected) if phase in TEST_PHASES), None)
    if first_test is not None and "install" not in selected:
        if "install" in skip:
            raise SystemExit(
                "error: the test phases need install unless --current-environment is given"
            )
        selected.insert(first_test, "install")
    return selected


def run_phases(phases: Sequence[str], run: Run) -> dict[str, str]:
    """Run *phases* in order and return each one's outcome."""
    results: dict[str, str] = {}
    for phase in phases:
        blocked = [p for p in PREREQUISITES.get(phase, ()) if results.get(p, "ok") != "ok"]
        if blocked:
            results[phase] = f"not run, {blocked[0]} failed"
            continue
        print(f"=== {phase}", flush=True)
        if phase in TEST_PHASES and "install" not in phases:
            print(f"testing saw imported from {saw_location(run.python)}", flush=True)
        started = time.perf_counter()
        status = RUNNERS[phase](run)
        elapsed = time.perf_counter() - started
        outcome = "ok" if status == 0 else f"failed with exit code {status}"
        results[phase] = f"{outcome} ({elapsed:.0f}s)" if status else "ok"
        print(f"=== {phase}: {outcome} in {elapsed:.0f}s", flush=True)
    return results


def main(argv: list[str] | None = None) -> int:
    """Run the selected phases and name every one that failed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        choices=PHASES,
        help="run only this phase, repeatable",
    )
    parser.add_argument(
        "--skip", action="append", default=[], choices=PHASES, help="skip this phase, repeatable"
    )
    parser.add_argument(
        "--current-environment",
        action="store_true",
        help="run the test phases with this interpreter instead of a fresh one built from the wheel",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="let the build phase run on a checkout with uncommitted changes",
    )
    arguments = parser.parse_args(argv)
    phases = select_phases(arguments.only, arguments.skip, arguments.current_environment)

    # the temporary directory is outside the checkout
    with tempfile.TemporaryDirectory(
        prefix="saw-release-", ignore_cleanup_errors=True
    ) as directory:
        run = Run(
            python=sys.executable, allow_dirty=arguments.allow_dirty, directory=Path(directory)
        )
        results = run_phases(phases, run)

    print("=== summary", flush=True)
    for phase, result in results.items():
        print(f"{phase}: {result}")
    failed = [phase for phase, result in results.items() if result != "ok"]
    if failed:
        print(f"error: release gate failed: {', '.join(failed)}", file=sys.stderr)
        return 1
    print("ok: every selected release gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
