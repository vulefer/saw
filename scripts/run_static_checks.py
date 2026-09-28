"""Run the formatter, linter and type checker exactly as CI does."""

import argparse
from pathlib import Path
import subprocess
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

# every tool reads its configuration from pyproject.toml, so the paths and
# rules live in one place; each check is (name, command, command with --fix)
CHECKS = (
    (
        "format",
        ("ruff", "format", "--check", "--diff"),
        ("ruff", "format"),
    ),
    (
        "lint",
        ("ruff", "check"),
        ("ruff", "check", "--fix"),
    ),
    (
        "types",
        ("mypy",),
        ("mypy",),
    ),
)


def main(argv: list[str] | None = None) -> int:
    """Run every check, report each failed one and keep going."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fix",
        action="store_true",
        help="reformat and apply safe lint fixes instead of only reporting them",
    )
    arguments = parser.parse_args(argv)

    failed = []
    for name, command, fix_command in CHECKS:
        print(f"--- {name}", flush=True)
        completed = subprocess.run(
            [sys.executable, "-m", *(fix_command if arguments.fix else command)],
            cwd=REPOSITORY_ROOT,
            check=False,
        )
        if completed.returncode != 0:
            failed.append(name)

    if failed:
        print(f"error: static checks failed: {', '.join(failed)}", file=sys.stderr)
        return 1
    print("ok: format, lint and types")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
