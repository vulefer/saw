"""Executable checks for the dependency policy in docs/dependencies.md."""

import ast
from pathlib import Path
import sys

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPOSITORY_ROOT / "src" / "saw"

# adding a runtime dependency means changing this set and the policy doc in
# the same pull request
ALLOWED_RUNTIME_DEPENDENCIES = {"pulp", "cbcbox"}
ALLOWED_THIRD_PARTY_IMPORTS = {"pulp", "cbcbox"}


def _project() -> dict:
    with (REPOSITORY_ROOT / "pyproject.toml").open("rb") as file:
        return tomllib.load(file)["project"]


def _direct_requirements() -> list[Requirement]:
    project = _project()
    declared = project["dependencies"] + project["optional-dependencies"]["dev"]
    return [Requirement(line) for line in declared]


# name -> every version pinned for it (a universal lock can pin one package
# twice under different markers)
def _lock_pins(name: str) -> dict[str, set[Version]]:
    pins: dict[str, set[Version]] = {}
    for line in (REPOSITORY_ROOT / "requirements" / name).read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        requirement = Requirement(line)
        (specifier,) = requirement.specifier
        assert specifier.operator == "==", line
        pins.setdefault(canonicalize_name(requirement.name), set()).add(Version(specifier.version))
    return pins


def test_runtime_dependencies_are_only_the_solver_stack() -> None:
    names = {canonicalize_name(Requirement(line).name) for line in _project()["dependencies"]}

    assert names == ALLOWED_RUNTIME_DEPENDENCIES


def test_every_direct_dependency_has_lower_and_upper_bounds() -> None:
    for requirement in _direct_requirements():
        operators = {specifier.operator for specifier in requirement.specifier}

        assert operators == {">=", "<"}, str(requirement)


def test_package_imports_only_stdlib_and_the_solver_stack() -> None:
    third_party = set()
    for path in PACKAGE_ROOT.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = [node.module]
            else:
                continue
            for module in modules:
                top = module.split(".", 1)[0]
                if top != "saw" and top not in sys.stdlib_module_names:
                    third_party.add(top)

    assert third_party <= ALLOWED_THIRD_PARTY_IMPORTS


def test_dev_lock_pins_every_direct_dependency_inside_its_bounds() -> None:
    pins = _lock_pins("dev.txt")

    for requirement in _direct_requirements():
        versions = pins[canonicalize_name(requirement.name)]

        assert all(requirement.specifier.contains(v) for v in versions), str(requirement)


def test_minimum_lock_pins_every_direct_dependency_at_its_lower_bound() -> None:
    pins = _lock_pins("minimum.txt")

    for requirement in _direct_requirements():
        (lower,) = [s for s in requirement.specifier if s.operator == ">="]

        assert pins[canonicalize_name(requirement.name)] == {Version(lower.version)}
