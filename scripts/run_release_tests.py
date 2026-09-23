"""Run every test required for a Saw release."""

from pathlib import Path
import subprocess
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RELEASE_TEST_PATHS = (
    "tests/unit",
    "tests/integration",
    "tests/regression",
    "tests/oracle",
    "tests/compatibility",
    "tests/contracts",
    "tests/test_minimal_example.py",
)


def main() -> int:
    """Run the release test paths and preserve pytest's exit status."""
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", *RELEASE_TEST_PATHS],
        cwd=REPOSITORY_ROOT,
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
