from pathlib import Path
import re
import subprocess
import sys

REPOSITORY_ROOT = Path(__file__).parents[1]
README = REPOSITORY_ROOT / "README.md"
EXAMPLE = REPOSITORY_ROOT / "examples" / "minimal.py"
# the python block of a readme section and the text block that shows its output
EXAMPLE_SECTION = re.compile(
    r"## Minimal Python example\n.*?```python\n(?P<code>.*?)```\n.*?```text\n(?P<output>.*?)```",
    re.DOTALL,
)


def _readme_example() -> tuple[str, str]:
    match = EXAMPLE_SECTION.search(README.read_text(encoding="utf-8"))
    assert match is not None, "README.md lost its minimal Python example"
    return match["code"], match["output"]


def test_the_readme_example_prints_what_the_readme_shows(tmp_path) -> None:
    code, output = _readme_example()
    script = tmp_path / "readme_example.py"
    script.write_text(code, encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(script)], cwd=tmp_path, capture_output=True, text=True, check=True
    )

    assert completed.stderr == ""
    assert completed.stdout == output


def test_the_readme_example_is_the_example_file() -> None:
    code, _ = _readme_example()

    # examples/minimal.py wraps the same statements in main()
    body = EXAMPLE.read_text(encoding="utf-8")
    for line in code.strip().splitlines():
        assert line.strip() in body, line
