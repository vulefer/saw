from pathlib import Path
import subprocess
import sys

CONFTEST = Path(__file__).parents[1] / "conftest.py"


def _run_pytest(tmp_path, test_source: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "conftest.py").write_text(CONFTEST.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "test_sample.py").write_text(test_source, encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", str(tmp_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_skipped_test_fails_the_run(tmp_path) -> None:
    completed = _run_pytest(
        tmp_path,
        "import pytest\n"
        "def test_passes(): pass\n"
        "def test_needs_cbc(): pytest.skip('cbc not found')\n",
    )

    assert completed.returncode == 1
    assert "1 test(s) skipped" in completed.stdout


def test_a_run_without_skips_passes(tmp_path) -> None:
    completed = _run_pytest(tmp_path, "def test_passes(): pass\n")

    assert completed.returncode == 0
