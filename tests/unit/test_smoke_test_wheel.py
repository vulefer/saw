from pathlib import Path

import pytest

from scripts import smoke_test_wheel

SCHEMAS = {
    "problem-v1.schema.json": '{"$schema": "https://json-schema.org/draft/2020-12/schema"}\n'
}


def _environment(tmp_path) -> smoke_test_wheel.Environment:
    return smoke_test_wheel.Environment(root=tmp_path / "venv", work=tmp_path / "work")


def _report(tmp_path, **changes) -> dict:
    report = {
        "import_errors": {},
        "location": str(tmp_path / "venv" / "site-packages" / "saw" / "__init__.py"),
        "version": "1.0.0",
        "py_typed": True,
        "schemas": dict(SCHEMAS),
    }
    report.update(changes)
    return report


def test_a_complete_installation_passes(tmp_path) -> None:
    problems = smoke_test_wheel.check_package(
        _report(tmp_path), _environment(tmp_path), "1.0.0", SCHEMAS
    )

    assert problems == []


def test_an_undeclared_dependency_fails(tmp_path) -> None:
    report = _report(
        tmp_path, import_errors={"saw.cli": "ModuleNotFoundError: No module named 'rich'"}
    )

    assert smoke_test_wheel.check_package(report, _environment(tmp_path), "1.0.0", SCHEMAS) == [
        "package: cannot import saw.cli: ModuleNotFoundError: No module named 'rich'"
    ]


def test_missing_package_data_fails(tmp_path) -> None:
    report = _report(tmp_path, py_typed=False, schemas={})

    assert smoke_test_wheel.check_package(report, _environment(tmp_path), "1.0.0", SCHEMAS) == [
        "package: py.typed is missing",
        "schemas: problem-v1.schema.json is missing",
    ]


def test_stale_or_unversioned_schemas_fail(tmp_path) -> None:
    schemas = {**SCHEMAS, "error-v1.schema.json": "{}\n"}
    report = _report(
        tmp_path,
        schemas={
            "problem-v1.schema.json": "{}\n",
            "error-v1.schema.json": "{}\n",
            "x.schema.json": "{}",
        },
    )

    assert smoke_test_wheel.check_package(report, _environment(tmp_path), "1.0.0", schemas) == [
        "schemas: unexpected x.schema.json",
        "schemas: error-v1.schema.json is not a draft 2020-12 schema",
        "schemas: problem-v1.schema.json differs from the source tree",
    ]


def test_an_import_from_the_checkout_fails(tmp_path) -> None:
    location = smoke_test_wheel.REPOSITORY_ROOT / "src" / "saw" / "__init__.py"
    report = _report(tmp_path, location=str(location), version="0.9.0")

    problems = smoke_test_wheel.check_package(report, _environment(tmp_path), "1.0.0", SCHEMAS)

    assert f"package: saw was imported from the checkout at {location.resolve()}" in problems
    assert "package: installed version is 0.9.0, expected 1.0.0" in problems
    assert any(problem.endswith(f"not from {tmp_path / 'venv'}") for problem in problems)


def test_find_wheel_needs_exactly_one_wheel(tmp_path) -> None:
    assert smoke_test_wheel.find_wheel(tmp_path)[1] is None

    (tmp_path / "saw-1.0.0-py3-none-any.whl").write_bytes(b"")
    assert smoke_test_wheel.find_wheel(tmp_path) == ([], tmp_path / "saw-1.0.0-py3-none-any.whl")

    (tmp_path / "saw-0.9.0-py3-none-any.whl").write_bytes(b"")
    problems, wheel = smoke_test_wheel.find_wheel(tmp_path)
    assert wheel is None
    assert problems[0].startswith("more than one saw wheel")


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ([], ("--constraint", str(smoke_test_wheel.DEFAULT_CONSTRAINT.resolve()))),
        (["--constraint", "other.txt"], ("--constraint", str(Path("other.txt").resolve()))),
        (["--unlocked"], ()),
    ],
)
def test_the_lock_choice_reaches_pip(monkeypatch, tmp_path, arguments, expected) -> None:
    received = []
    monkeypatch.setattr(
        smoke_test_wheel,
        "smoke_test",
        lambda wheel, options, directory: received.append(options) or [],
    )
    wheel = tmp_path / "saw-1.0.0-py3-none-any.whl"

    assert smoke_test_wheel.main(["--wheel", str(wheel), *arguments]) == 0
    assert received == [expected]


def test_a_missing_wheel_fails_without_building_an_environment(
    monkeypatch, tmp_path, capsys
) -> None:
    monkeypatch.setattr(smoke_test_wheel, "DIST_DIRECTORY", Path(tmp_path))

    assert smoke_test_wheel.main([]) == 1
    assert "run scripts/build_distributions.py first" in capsys.readouterr().err
