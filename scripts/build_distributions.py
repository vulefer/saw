"""Build the sdist and wheel from a clean checkout and check both artifacts."""

import argparse
import base64
import configparser
import csv
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile

from packaging.metadata import Metadata
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DIST_DIRECTORY = REPOSITORY_ROOT / "dist"
SCHEMA_DIRECTORY = REPOSITORY_ROOT / "src" / "saw" / "schemas"
CONSOLE_SCRIPT = ("saw", "saw.cli:main")
# a schema file name carries the format and its major version
SCHEMA_NAME = re.compile(r"^[a-z]+-v[0-9]+\.schema\.json$")

# tracked files that only matter to the repository, never to a release
REPOSITORY_ONLY_FILES = {".gitignore", "PROJECT_PLAN.md"}
REPOSITORY_ONLY_PREFIXES = (".github/",)
# files setuptools writes into every sdist
GENERATED_SDIST_FILES = {"PKG-INFO", "setup.cfg"}
GENERATED_SDIST_PREFIXES = ("src/saw.egg-info/",)


def load_project(root: Path = REPOSITORY_ROOT) -> dict:
    """Return the [project] table of pyproject.toml."""
    with (root / "pyproject.toml").open("rb") as file:
        return tomllib.load(file)["project"]


def expected_schemas(directory: Path = SCHEMA_DIRECTORY) -> dict[str, bytes]:
    """Return every versioned schema the wheel must carry, by file name."""
    return {path.name: path.read_bytes() for path in sorted(directory.glob("*.schema.json"))}


def find_artifacts(directory: Path, project: dict) -> tuple[list[str], Path | None, Path | None]:
    """Return problems plus the one sdist and the one wheel in *directory*."""
    name = canonicalize_name(project["name"]).replace("-", "_")
    version = str(Version(project["version"]))
    sdist = directory / f"{name}-{version}.tar.gz"
    wheel = directory / f"{name}-{version}-py3-none-any.whl"
    found = {path.name for path in directory.iterdir()} if directory.is_dir() else set()
    problems = [f"missing artifact {path.name}" for path in (sdist, wheel) if path.name not in found]
    problems += [f"unexpected artifact {extra}" for extra in sorted(found - {sdist.name, wheel.name})]
    return problems, (sdist if sdist.name in found else None), (wheel if wheel.name in found else None)


def check_metadata(raw: bytes, project: dict, source: str) -> list[str]:
    """Validate core metadata and compare it with pyproject.toml."""
    try:
        metadata = Metadata.from_email(raw, validate=True)
    except Exception as error:  # an ExceptionGroup of every invalid field
        details = getattr(error, "exceptions", [error])
        return [f"{source}: invalid metadata: {detail}" for detail in details]

    problems = []
    expected = {
        "name": canonicalize_name(project["name"]),
        "version": Version(project["version"]),
        "license expression": project["license"],
        "requires-python": SpecifierSet(project["requires-python"]),
    }
    actual = {
        "name": canonicalize_name(metadata.name),
        "version": metadata.version,
        "license expression": metadata.license_expression,
        "requires-python": metadata.requires_python,
    }
    for field, value in expected.items():
        if actual[field] != value:
            problems.append(f"{source}: {field} is {actual[field]!r}, expected {value!r}")

    # requirements without an extra marker are the ones every install gets
    runtime = {
        str(requirement)
        for requirement in metadata.requires_dist or []
        if requirement.marker is None or "extra" not in str(requirement.marker)
    }
    declared = {str(Requirement(line)) for line in project["dependencies"]}
    if runtime != declared:
        problems.append(f"{source}: runtime requirements {sorted(runtime)} != {sorted(declared)}")
    if not metadata.license_files or "LICENSE" not in metadata.license_files:
        problems.append(f"{source}: LICENSE is not declared as a license file")
    return problems


def _record_problems(archive: zipfile.ZipFile, record_name: str) -> list[str]:
    rows = list(csv.reader(io.StringIO(archive.read(record_name).decode("utf-8"))))
    listed = {row[0] for row in rows}
    problems = [f"wheel: {name} is missing from RECORD" for name in archive.namelist() if name not in listed]
    for path, digest, size in rows:
        if path == record_name:
            continue
        if path not in archive.namelist():
            problems.append(f"wheel: RECORD lists missing file {path}")
            continue
        data = archive.read(path)
        actual = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        if digest != actual or size != str(len(data)):
            problems.append(f"wheel: RECORD hash or size of {path} does not match")
    return problems


def check_wheel(path: Path, project: dict, schemas: dict[str, bytes]) -> list[str]:
    """Check the wheel's metadata, entry point, schemas and file list."""
    name = canonicalize_name(project["name"]).replace("-", "_")
    dist_info = f"{name}-{Version(project['version'])}.dist-info"
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        problems = []

        for required in (f"{dist_info}/METADATA", f"{dist_info}/RECORD", f"{dist_info}/entry_points.txt"):
            if required not in names:
                problems.append(f"wheel: missing {required}")
        if problems:
            return problems

        problems += check_metadata(archive.read(f"{dist_info}/METADATA"), project, "wheel")
        problems += _record_problems(archive, f"{dist_info}/RECORD")

        entry_points = configparser.ConfigParser(delimiters=("=",))
        entry_points.optionxform = str
        entry_points.read_string(archive.read(f"{dist_info}/entry_points.txt").decode("utf-8"))
        script, target = CONSOLE_SCRIPT
        if entry_points.get("console_scripts", script, fallback=None) != target:
            problems.append(f"wheel: console script {script} does not point to {target}")

        for required in ("saw/__init__.py", "saw/cli.py", "saw/py.typed", f"{dist_info}/licenses/LICENSE"):
            if required not in names:
                problems.append(f"wheel: missing {required}")

        if not schemas:
            problems.append("wheel: no versioned schemas were found to check")
        shipped = {PurePosixPath(n).name for n in names if n.startswith("saw/schemas/")}
        for schema, content in schemas.items():
            if not SCHEMA_NAME.match(schema):
                problems.append(f"wheel: schema {schema} has no version in its name")
            member = f"saw/schemas/{schema}"
            if member not in names:
                problems.append(f"wheel: missing {member}")
            elif archive.read(member) != content:
                problems.append(f"wheel: {member} differs from the source tree")
        for extra in sorted(shipped - set(schemas)):
            problems.append(f"wheel: unexpected schema saw/schemas/{extra}")

        for member in sorted(names):
            top = member.split("/", 1)[0]
            if top not in {"saw", dist_info}:
                problems.append(f"wheel: unexpected top-level entry {member}")
            if "__pycache__" in member or member.endswith((".pyc", ".pyo")):
                problems.append(f"wheel: bytecode {member} was packaged")
    return problems


def _is_release_file(path: str) -> bool:
    return path not in REPOSITORY_ONLY_FILES and not path.startswith(REPOSITORY_ONLY_PREFIXES)


def _is_generated(path: str) -> bool:
    return path in GENERATED_SDIST_FILES or path.startswith(GENERATED_SDIST_PREFIXES)


def check_sdist(path: Path, project: dict, tracked_files: set[str]) -> list[str]:
    """Check the sdist's metadata and that it holds exactly the release files."""
    name = canonicalize_name(project["name"]).replace("-", "_")
    top = f"{name}-{Version(project['version'])}"
    with tarfile.open(path, "r:gz") as archive:
        members = [member for member in archive.getmembers() if member.isfile()]
        files = {}
        problems = []
        for member in members:
            if not member.name.startswith(f"{top}/"):
                problems.append(f"sdist: {member.name} is outside {top}/")
                continue
            files[member.name[len(top) + 1:]] = member
        if "PKG-INFO" not in files:
            return problems + ["sdist: missing PKG-INFO"]
        problems += check_metadata(archive.extractfile(files["PKG-INFO"]).read(), project, "sdist")

    # the sdist must rebuild the wheel and run the release suite, so every
    # tracked release file belongs in it and nothing untracked may leak in
    expected = {path for path in tracked_files if _is_release_file(path)}
    for missing in sorted(expected - set(files)):
        problems.append(f"sdist: missing tracked file {missing}")
    for extra in sorted(set(files) - expected):
        if not _is_generated(extra):
            problems.append(f"sdist: unexpected file {extra}")
    return problems


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=REPOSITORY_ROOT, capture_output=True, text=True, check=True
    )
    return completed.stdout


# ignored files (dist/, build/, *.egg-info) are left out on purpose
def _worktree_changes() -> list[str]:
    return _git("status", "--porcelain", "--untracked-files=all").splitlines()


def main(argv: list[str] | None = None) -> int:
    """Build both artifacts into dist/ and fail on any packaging problem."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="build even if the checkout has uncommitted changes",
    )
    arguments = parser.parse_args(argv)

    before = _worktree_changes()
    if before and not arguments.allow_dirty:
        print("error: the checkout is not clean, commit or stash these first:", file=sys.stderr)
        print("\n".join(before), file=sys.stderr)
        return 1

    shutil.rmtree(DIST_DIRECTORY, ignore_errors=True)
    # pins every timestamp in the wheel to the last commit, so the same
    # commit and toolchain always produce the same wheel bytes
    environment = {**os.environ, "SOURCE_DATE_EPOCH": _git("log", "-1", "--format=%ct").strip()}
    # without --sdist or --wheel, build makes the sdist first and then the
    # wheel from the unpacked sdist, which proves the sdist is complete
    completed = subprocess.run(
        [sys.executable, "-m", "build", "--outdir", str(DIST_DIRECTORY), str(REPOSITORY_ROOT)],
        env=environment,
        check=False,
    )
    if completed.returncode != 0:
        return completed.returncode

    project = load_project()
    problems, sdist, wheel = find_artifacts(DIST_DIRECTORY, project)
    if sdist is not None:
        problems += check_sdist(sdist, project, set(_git("ls-files").splitlines()))
    if wheel is not None:
        problems += check_wheel(wheel, project, expected_schemas())

    after = _worktree_changes()
    if after != before:
        problems.append("the build changed files that are not ignored:")
        problems += [f"  {line}" for line in after if line not in before]

    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if problems:
        return 1
    for artifact in (sdist, wheel):
        print(f"ok: {artifact.relative_to(REPOSITORY_ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
