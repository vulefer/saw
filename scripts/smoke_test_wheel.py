"""Install the built wheel into a fresh virtual environment and smoke test it."""

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any
import venv

from jsonschema import Draft202012Validator
from packaging.version import Version

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DIST_DIRECTORY = REPOSITORY_ROOT / "dist"
SCHEMA_DIRECTORY = REPOSITORY_ROOT / "src" / "saw" / "schemas"
DEFAULT_CONSTRAINT = REPOSITORY_ROOT / "requirements" / "dev.txt"
EXAMPLE = REPOSITORY_ROOT / "examples" / "minimal.py"
EXAMPLE_OUTPUT = "stock-10 x 1: demand-4 x 1, demand-6 x 1\n"
PROBLEM = REPOSITORY_ROOT / "examples" / "json" / "problem-v1-valid.json"
SOLUTION = REPOSITORY_ROOT / "examples" / "json" / "solution-v1-optimal.json"
SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
# a hung install or solve fails the gate instead of blocking ci
TIMEOUT_SECONDS = 600

# interpreter settings that could put the checkout or another site-packages
# on sys.path of the fresh environment
LEAKING_VARIABLES = ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE", "VIRTUAL_ENV")

# runs inside the fresh environment with the standard library only. it
# imports every module, so an undeclared dependency fails even when the
# happy path never reaches the module that needs it
PROBE = r"""
import importlib
from importlib import metadata, resources
import json
import pkgutil

report = {"import_errors": {}}
try:
    import saw
except Exception as error:
    report["import_errors"]["saw"] = f"{type(error).__name__}: {error}"
else:
    report["location"] = saw.__file__
    report["version"] = metadata.version("saw")

    def failed(name):
        report["import_errors"][name] = "could not be walked"

    for module in pkgutil.walk_packages(saw.__path__, "saw.", onerror=failed):
        try:
            importlib.import_module(module.name)
        except Exception as error:
            report["import_errors"][module.name] = f"{type(error).__name__}: {error}"

    package = resources.files("saw")
    report["py_typed"] = package.joinpath("py.typed").is_file()
    schemas = package.joinpath("schemas")
    report["schemas"] = {
        entry.name: entry.read_text(encoding="utf-8")
        for entry in (schemas.iterdir() if schemas.is_dir() else ())
        if entry.name.endswith(".schema.json")
    }
print(json.dumps(report))
"""


@dataclass(frozen=True)
class Environment:
    """A fresh virtual environment and a work directory outside the checkout."""

    root: Path
    work: Path

    @property
    def scripts(self) -> Path:
        return self.root / ("Scripts" if os.name == "nt" else "bin")

    @property
    def python(self) -> Path:
        return self.scripts / ("python.exe" if os.name == "nt" else "python")

    @property
    def saw(self) -> Path:
        return self.scripts / ("saw.exe" if os.name == "nt" else "saw")


def project_version(root: Path = REPOSITORY_ROOT) -> str:
    """Return the normalized version declared in pyproject.toml."""
    with (root / "pyproject.toml").open("rb") as file:
        return str(Version(tomllib.load(file)["project"]["version"]))


def expected_schemas(directory: Path = SCHEMA_DIRECTORY) -> dict[str, str]:
    """Return every schema the wheel must carry, by file name."""
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(directory.glob("*.schema.json"))
    }


def find_wheel(directory: Path) -> tuple[list[str], Path | None]:
    """Return problems plus the one saw wheel in *directory*."""
    wheels = sorted(directory.glob("saw-*.whl")) if directory.is_dir() else []
    if len(wheels) == 1:
        return [], wheels[0]
    if not wheels:
        return [f"no saw wheel in {directory}, run scripts/build_distributions.py first"], None
    return [f"more than one saw wheel in {directory}: {', '.join(w.name for w in wheels)}"], None


def _environment_variables() -> dict[str, str]:
    variables = {k: v for k, v in os.environ.items() if k not in LEAKING_VARIABLES}
    variables["PYTHONNOUSERSITE"] = "1"
    return variables


def run(environment: Environment, *command: object) -> subprocess.CompletedProcess[str]:
    """Run *command* in the work directory with a clean interpreter environment."""
    return subprocess.run(
        [str(part) for part in command],
        cwd=environment.work,
        env=_environment_variables(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=TIMEOUT_SECONDS,
    )


def _failure(gate: str, completed: subprocess.CompletedProcess[str]) -> str:
    output = (completed.stderr or completed.stdout).strip().splitlines()
    detail = f": {output[-1]}" if output else ""
    return f"{gate}: exit code {completed.returncode}{detail}"


def create_environment(directory: Path) -> Environment:
    """Create an isolated virtual environment with pip under *directory*."""
    # no system site packages, so only what the wheel declares is importable
    venv.EnvBuilder(with_pip=True, clear=True, system_site_packages=False).create(
        directory / "venv"
    )
    work = directory / "work"
    work.mkdir()
    return Environment(root=(directory / "venv").resolve(), work=work.resolve())


def install_wheel(environment: Environment, wheel: Path, *pip_options: str) -> list[str]:
    """Install *wheel* without extras and check the installed requirements."""
    pip = (environment.python, "-m", "pip", "--disable-pip-version-check", "--no-input")
    completed = run(environment, *pip, "install", *pip_options, wheel.resolve())
    if completed.returncode != 0:
        return [_failure("install", completed)]
    completed = run(environment, *pip, "check")
    return [] if completed.returncode == 0 else [_failure("install: pip check", completed)]


def probe_package(environment: Environment) -> tuple[dict[str, Any] | None, list[str]]:
    """Import every installed saw module and collect its package data."""
    # -I leaves the work directory, the user site and PYTHON* variables out of sys.path
    completed = run(environment, environment.python, "-I", "-c", PROBE)
    if completed.returncode != 0:
        return None, [_failure("package", completed)]
    return json.loads(completed.stdout), []


def check_package(
    report: dict[str, Any], environment: Environment, version: str, schemas: dict[str, str]
) -> list[str]:
    """Check that saw imports from the environment and carries its data."""
    problems = [
        f"package: cannot import {name}: {error}" for name, error in report["import_errors"].items()
    ]
    if "location" not in report:
        return problems

    location = Path(report["location"]).resolve()
    if not location.is_relative_to(environment.root):
        problems.append(f"package: saw was imported from {location}, not from {environment.root}")
    if location.is_relative_to(REPOSITORY_ROOT):
        problems.append(f"package: saw was imported from the checkout at {location}")
    if report["version"] != version:
        problems.append(f"package: installed version is {report['version']}, expected {version}")
    if not report["py_typed"]:
        problems.append("package: py.typed is missing")

    installed = report["schemas"]
    for name in sorted(set(schemas) - set(installed)):
        problems.append(f"schemas: {name} is missing")
    for name in sorted(set(installed) - set(schemas)):
        problems.append(f"schemas: unexpected {name}")
    for name in sorted(set(schemas) & set(installed)):
        if installed[name] != schemas[name]:
            problems.append(f"schemas: {name} differs from the source tree")
        elif json.loads(installed[name]).get("$schema") != SCHEMA_DIALECT:
            problems.append(f"schemas: {name} is not a draft 2020-12 schema")
    return problems


def check_example(environment: Environment) -> list[str]:
    """Run the documented Python example from a copy outside the checkout."""
    example = environment.work / EXAMPLE.name
    example.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    completed = run(environment, environment.python, "-I", example)
    if completed.returncode != 0:
        return [_failure("example", completed)]
    if completed.stdout != EXAMPLE_OUTPUT:
        return [f"example: printed {completed.stdout!r}, expected {EXAMPLE_OUTPUT!r}"]
    return []


def check_cli_help(environment: Environment) -> list[str]:
    """Check that the console script and python -m saw both print help."""
    problems = []
    for gate, command in (
        ("cli help", (environment.saw, "--help")),
        ("cli help", (environment.saw, "solve", "--help")),
        ("cli help: python -m saw", (environment.python, "-I", "-m", "saw", "--help")),
    ):
        completed = run(environment, *command)
        if completed.returncode != 0:
            problems.append(_failure(gate, completed))
        elif "usage:" not in completed.stdout:
            problems.append(f"{gate}: no usage text on standard output")
    return problems


def check_cli_version(environment: Environment, version: str) -> list[str]:
    """Check that saw --version reports the installed release."""
    completed = run(environment, environment.saw, "--version")
    if completed.returncode != 0:
        return [_failure("cli version", completed)]
    if completed.stdout.strip() != f"saw {version}":
        return [f"cli version: printed {completed.stdout.strip()!r}, expected 'saw {version}'"]
    return []


def check_json_solve(environment: Environment, installed_schemas: dict[str, str]) -> list[str]:
    """Solve the documented JSON problem through the installed saw command."""
    problem = environment.work / PROBLEM.name
    problem.write_text(PROBLEM.read_text(encoding="utf-8"), encoding="utf-8")
    completed = run(environment, environment.saw, "solve", problem.name)
    if completed.returncode != 0:
        return [_failure("json solve", completed)]
    if not completed.stdout.endswith("\n") or completed.stdout.count("\n") != 1:
        return ["json solve: standard output is not one newline-terminated document"]

    document = json.loads(completed.stdout)
    problems = []
    # the schema is the one loaded from the installed wheel, so this also
    # proves the packaged schema describes what the packaged cli writes
    schema = installed_schemas.get("solution-v1.schema.json")
    if schema is None:
        problems.append("json solve: the installed solution schema is missing")
    else:
        for error in Draft202012Validator(json.loads(schema)).iter_errors(document):
            problems.append(f"json solve: output does not match the schema: {error.message}")

    # diagnostics are run specific, the rest is the documented plan
    expected = json.loads(SOLUTION.read_text(encoding="utf-8"))
    expected.pop("diagnostics")
    actual = {key: value for key, value in document.items() if key != "diagnostics"}
    if actual != expected:
        problems.append(f"json solve: printed {actual}, expected {expected}")
    return problems


def smoke_test(wheel: Path, pip_options: tuple[str, ...], directory: Path) -> list[str]:
    """Install *wheel* into a fresh environment under *directory* and run every gate."""
    print(f"creating a fresh environment in {directory}", flush=True)
    environment = create_environment(directory)
    problems = install_wheel(environment, wheel, *pip_options)
    if problems:
        return problems

    report, problems = probe_package(environment)
    if report is None:
        return problems
    version = project_version()
    gates = {
        "package": check_package(report, environment, version, expected_schemas()),
        "example": check_example(environment),
        "cli help": check_cli_help(environment),
        "cli version": check_cli_version(environment, version),
        "json solve": check_json_solve(environment, report.get("schemas", {})),
    }
    for gate, gate_problems in gates.items():
        if not gate_problems:
            print(f"ok: {gate}", flush=True)
        problems += gate_problems
    return problems


def main(argv: list[str] | None = None) -> int:
    """Smoke test the built wheel and fail on any broken gate."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wheel", type=Path, help="the wheel to test (default: the one saw wheel in dist/)"
    )
    parser.add_argument(
        "--constraint",
        type=Path,
        default=DEFAULT_CONSTRAINT,
        help="constraint file for the runtime dependencies (default: requirements/dev.txt)",
    )
    arguments = parser.parse_args(argv)

    if arguments.wheel is None:
        problems, wheel = find_wheel(DIST_DIRECTORY)
    else:
        problems, wheel = [], arguments.wheel
    if wheel is not None:
        # the temporary directory is outside the checkout, so nothing can be
        # imported from the source tree by accident
        with tempfile.TemporaryDirectory(
            prefix="saw-wheel-smoke-", ignore_cleanup_errors=True
        ) as directory:
            options = ("--constraint", str(arguments.constraint.resolve()))
            problems += smoke_test(wheel, options, Path(directory))

    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    # find_wheel reports a missing wheel as a problem
    if problems or wheel is None:
        return 1
    print(f"ok: {wheel.name} installs and works in a clean environment")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
