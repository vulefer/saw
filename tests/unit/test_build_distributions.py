import base64
import hashlib
import io
import tarfile
import zipfile

import pytest

from scripts import build_distributions

PROJECT = {
    "name": "saw",
    "version": "1.0.0",
    "license": "LicenseRef-Proprietary",
    "requires-python": ">=3.10,<3.15",
    "dependencies": ["PuLP>=3.3.2,<4", "cbcbox>=2.929,<3"],
}
SCHEMAS = {"problem-v1.schema.json": b'{"type": "object"}\n'}
DIST_INFO = "saw-1.0.0.dist-info"
METADATA = b"""\
Metadata-Version: 2.4
Name: saw
Version: 1.0.0
License-Expression: LicenseRef-Proprietary
License-File: LICENSE
Requires-Python: <3.15,>=3.10
Requires-Dist: PuLP<4,>=3.3.2
Requires-Dist: cbcbox<3,>=2.929
Provides-Extra: dev
Requires-Dist: pytest<10,>=9; extra == "dev"
"""
ENTRY_POINTS = b"[console_scripts]\nsaw = saw.cli:main\n"


def _wheel_files() -> dict[str, bytes]:
    return {
        "saw/__init__.py": b"",
        "saw/cli.py": b"def main(): pass\n",
        "saw/py.typed": b"",
        "saw/schemas/problem-v1.schema.json": SCHEMAS["problem-v1.schema.json"],
        f"{DIST_INFO}/METADATA": METADATA,
        f"{DIST_INFO}/entry_points.txt": ENTRY_POINTS,
        f"{DIST_INFO}/licenses/LICENSE": b"proprietary\n",
    }


def _write_wheel(path, files: dict[str, bytes], record: bool = True):
    rows = []
    for name, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        rows.append(f"{name},sha256={digest},{len(data)}")
    rows.append(f"{DIST_INFO}/RECORD,,")
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        if record:
            archive.writestr(f"{DIST_INFO}/RECORD", "\n".join(rows) + "\n")
    return path


def _write_sdist(path, files: dict[str, bytes]):
    with tarfile.open(path, "w:gz") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(f"saw-1.0.0/{name}")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return path


def test_a_complete_wheel_passes(tmp_path) -> None:
    wheel = _write_wheel(tmp_path / "saw.whl", _wheel_files())

    assert build_distributions.check_wheel(wheel, PROJECT, SCHEMAS) == []


@pytest.mark.parametrize(
    ("missing", "problem"),
    [
        ("saw/schemas/problem-v1.schema.json", "wheel: missing saw/schemas/problem-v1.schema.json"),
        ("saw/py.typed", "wheel: missing saw/py.typed"),
        (f"{DIST_INFO}/licenses/LICENSE", f"wheel: missing {DIST_INFO}/licenses/LICENSE"),
    ],
)
def test_a_wheel_missing_a_required_file_fails(tmp_path, missing, problem) -> None:
    files = _wheel_files()
    del files[missing]
    wheel = _write_wheel(tmp_path / "saw.whl", files)

    assert problem in build_distributions.check_wheel(wheel, PROJECT, SCHEMAS)


def test_a_wheel_without_the_console_script_fails(tmp_path) -> None:
    files = _wheel_files()
    files[f"{DIST_INFO}/entry_points.txt"] = b"[console_scripts]\nsaw = saw.other:main\n"
    wheel = _write_wheel(tmp_path / "saw.whl", files)

    assert "wheel: console script saw does not point to saw.cli:main" in (
        build_distributions.check_wheel(wheel, PROJECT, SCHEMAS)
    )


def test_a_wheel_with_a_stale_schema_fails(tmp_path) -> None:
    files = _wheel_files()
    files["saw/schemas/problem-v1.schema.json"] = b"{}\n"
    wheel = _write_wheel(tmp_path / "saw.whl", files)

    assert "wheel: saw/schemas/problem-v1.schema.json differs from the source tree" in (
        build_distributions.check_wheel(wheel, PROJECT, SCHEMAS)
    )


def test_a_wheel_with_stray_files_fails(tmp_path) -> None:
    files = _wheel_files()
    files["tests/test_x.py"] = b""
    files["saw/__pycache__/cli.cpython-314.pyc"] = b""
    wheel = _write_wheel(tmp_path / "saw.whl", files)

    problems = build_distributions.check_wheel(wheel, PROJECT, SCHEMAS)

    assert "wheel: unexpected top-level entry tests/test_x.py" in problems
    assert "wheel: bytecode saw/__pycache__/cli.cpython-314.pyc was packaged" in problems


def test_a_wheel_whose_record_does_not_match_fails(tmp_path) -> None:
    wheel = _write_wheel(tmp_path / "saw.whl", _wheel_files())
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr("saw/extra.py", b"")

    assert "wheel: saw/extra.py is missing from RECORD" in (
        build_distributions.check_wheel(wheel, PROJECT, SCHEMAS)
    )


def test_metadata_that_disagrees_with_pyproject_fails() -> None:
    raw = METADATA.replace(b"Version: 1.0.0", b"Version: 0.9.0").replace(
        b"Requires-Dist: cbcbox<3,>=2.929\n", b""
    )

    problems = build_distributions.check_metadata(raw, PROJECT, "wheel")

    assert any(problem.startswith("wheel: version is") for problem in problems)
    assert any(problem.startswith("wheel: runtime requirements") for problem in problems)


def test_invalid_metadata_fails() -> None:
    raw = METADATA.replace(b"Version: 1.0.0", b"Version: not-a-version")

    problems = build_distributions.check_metadata(raw, PROJECT, "sdist")

    assert problems
    assert all(problem.startswith("sdist: invalid metadata") for problem in problems)


def test_the_sdist_must_hold_exactly_the_tracked_release_files(tmp_path) -> None:
    tracked = {
        "pyproject.toml",
        "src/saw/cli.py",
        "tests/test_cli.py",
        ".gitignore",
        ".github/workflows/ci.yml",
    }
    sdist = _write_sdist(
        tmp_path / "saw.tar.gz",
        {
            "PKG-INFO": METADATA,
            "setup.cfg": b"",
            "src/saw.egg-info/SOURCES.txt": b"",
            "pyproject.toml": b"",
            "src/saw/cli.py": b"",
            "linear_api.txt": b"secret",
        },
    )

    problems = build_distributions.check_sdist(sdist, PROJECT, tracked)

    assert problems == [
        "sdist: missing tracked file tests/test_cli.py",
        "sdist: unexpected file linear_api.txt",
    ]


def test_find_artifacts_rejects_missing_and_extra_files(tmp_path) -> None:
    (tmp_path / "saw-1.0.0.tar.gz").write_bytes(b"")
    (tmp_path / "saw-0.9.0-py3-none-any.whl").write_bytes(b"")

    problems, sdist, wheel = build_distributions.find_artifacts(tmp_path, PROJECT)

    assert problems == [
        "missing artifact saw-1.0.0-py3-none-any.whl",
        "unexpected artifact saw-0.9.0-py3-none-any.whl",
    ]
    assert sdist == tmp_path / "saw-1.0.0.tar.gz"
    assert wheel is None
