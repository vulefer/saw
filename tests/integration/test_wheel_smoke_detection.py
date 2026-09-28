"""Proves the wheel smoke test catches a broken wheel in a real environment.

Each test builds a small fake saw wheel with one packaging mistake, installs
it offline into a fresh virtual environment the same way
scripts/smoke_test_wheel.py does, and checks that the probe reports it.
"""

import base64
import hashlib
import zipfile

import pytest

from scripts import smoke_test_wheel


DIST_INFO = "saw-1.0.0.dist-info"
SCHEMAS = smoke_test_wheel.expected_schemas()
METADATA = b"Metadata-Version: 2.1\nName: saw\nVersion: 1.0.0\n"
WHEEL = b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"


def _complete_package() -> dict[str, bytes]:
    files = {
        "saw/__init__.py": b"",
        "saw/solver.py": b"import json\n",
        "saw/py.typed": b"",
    }
    files.update({f"saw/schemas/{name}": text.encode("utf-8") for name, text in SCHEMAS.items()})
    return files


def _write_wheel(directory, package: dict[str, bytes]):
    files = {**package, f"{DIST_INFO}/METADATA": METADATA, f"{DIST_INFO}/WHEEL": WHEEL}
    rows = []
    for name, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        rows.append(f"{name},sha256={digest},{len(data)}")
    rows.append(f"{DIST_INFO}/RECORD,,")
    path = directory / "saw-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        archive.writestr(f"{DIST_INFO}/RECORD", "\n".join(rows) + "\n")
    return path


# creating an environment takes seconds, so the tests share one and each
# replaces the installed fake wheel
@pytest.fixture(scope="module")
def environment(tmp_path_factory):
    return smoke_test_wheel.create_environment(tmp_path_factory.mktemp("smoke"))


@pytest.fixture
def probe(environment, tmp_path):
    def probe(package: dict[str, bytes]) -> list[str]:
        wheel = _write_wheel(tmp_path, package)
        # the fake wheel declares no dependencies, so it installs offline
        assert smoke_test_wheel.install_wheel(environment, wheel, "--no-index", "--force-reinstall") == []
        report, problems = smoke_test_wheel.probe_package(environment)
        assert problems == []
        return smoke_test_wheel.check_package(report, environment, "1.0.0", SCHEMAS)

    return probe


def test_a_complete_fake_wheel_passes(probe) -> None:
    assert probe(_complete_package()) == []


def test_an_undeclared_runtime_dependency_is_detected(probe) -> None:
    package = _complete_package()
    # a module the happy path never touches still has to import
    package["saw/solver.py"] = b"import saw_undeclared_dependency\n"

    problems = probe(package)

    assert len(problems) == 1
    assert problems[0].startswith("package: cannot import saw.solver: ModuleNotFoundError")


@pytest.mark.parametrize("missing", ["saw/py.typed", "saw/schemas/solution-v1.schema.json"])
def test_missing_package_data_is_detected(probe, missing) -> None:
    package = _complete_package()
    del package[missing]

    problems = probe(package)

    assert problems == [
        "package: py.typed is missing"
        if missing == "saw/py.typed"
        else "schemas: solution-v1.schema.json is missing"
    ]
