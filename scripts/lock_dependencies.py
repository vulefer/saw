"""Regenerate the checked-in dependency locks under requirements/."""

from pathlib import Path
import subprocess
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
LOCK_COMMAND = "python scripts/lock_dependencies.py"

# one universal lock covers every supported os and python 3.10 to 3.14;
# dev.txt pins the newest allowed versions, minimum.txt the declared lower
# bounds of the direct dependencies and is always re-resolved, since uv would
# otherwise keep the versions already pinned in it
LOCKS = {
    "requirements/dev.txt": [],
    "requirements/minimum.txt": ["--resolution", "lowest-direct", "--upgrade"],
}


def main() -> int:
    """Compile every lock with uv and stop at the first failure."""
    for output, extra_args in LOCKS.items():
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "uv",
                "pip",
                "compile",
                "pyproject.toml",
                "--extra",
                "dev",
                "--universal",
                "--python-version",
                "3.10",
                "--custom-compile-command",
                LOCK_COMMAND,
                "--quiet",
                *extra_args,
                *sys.argv[1:],
                "--output-file",
                output,
            ],
            cwd=REPOSITORY_ROOT,
            check=False,
        )
        if completed.returncode != 0:
            return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
