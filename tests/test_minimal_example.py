from pathlib import Path
import subprocess
import sys


REPOSITORY_ROOT = Path(__file__).parents[1]
EXAMPLE = REPOSITORY_ROOT / "examples" / "minimal.py"


def test_minimal_example_runs_and_prints_stable_ids() -> None:
    completed = subprocess.run(
        [sys.executable, str(EXAMPLE)],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stderr == ""
    assert completed.stdout == (
        "stock-10 x 1: demand-4 x 1, demand-6 x 1\n"
    )
