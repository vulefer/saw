"""Report the solver stack saw will use and fail if CBC is not usable."""

from importlib import metadata
import platform
import subprocess
import sys

from saw.backends.base import BackendOptions
from saw.backends.pulp_backend import PulpCbcAdapter

DISTRIBUTIONS = ("saw", "PuLP", "cbcbox")


def _version(distribution: str) -> str:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return "not installed"


# the first line cbc prints, which names its build
def _cbc_banner(path: str) -> str:
    try:
        completed = subprocess.run(
            [path, "-quit"], capture_output=True, text=True, check=False, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"could not run: {error}"
    lines = completed.stdout.strip().splitlines()
    return lines[0] if lines else f"no output, exit code {completed.returncode}"


def main() -> int:
    """Print the interpreter, package and CBC details, exit 1 without CBC."""
    print(f"python: {platform.python_implementation()} {platform.python_version()}")
    print(f"platform: {platform.system()} {platform.release()} {platform.machine()}")
    for distribution in DISTRIBUTIONS:
        print(f"{distribution}: {_version(distribution)}")

    adapter = PulpCbcAdapter()
    availability = adapter.check_availability()
    if not availability.available:
        # the release suite needs cbc, so this is a failure and never a skip
        print(f"error: CBC is not available: {availability.reason}", file=sys.stderr)
        return 1

    path = str(adapter.create_solver(BackendOptions()).path)
    print(f"cbc executable: {path}")
    print(f"cbc build: {_cbc_banner(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
