from __future__ import annotations

import io
import json
import os
import subprocess
import sys

import pytest

from saw import cli
from saw.exceptions import BackendUnavailableError, InternalVerificationError
from saw.models import (
    BackendStatus,
    Diagnostics,
    ObjectiveStage,
    ObjectiveValue,
    Solution,
    SolutionStatus,
    TerminationReason,
    VerificationReport,
)
from saw.solve import SolveOptions

_VALID_PROBLEM_JSON = json.dumps(
    {
        "format_version": 1,
        "stocks": [{"stock_id": "stock-10", "material_id": "steel", "length": 10, "quantity": 1}],
        "demands": [
            {"demand_id": "demand-6", "material_id": "steel", "length": 6, "quantity": 1},
            {"demand_id": "demand-4", "material_id": "steel", "length": 4, "quantity": 1},
        ],
    }
)


def _diagnostics(
    *,
    termination_reason: TerminationReason,
    backend_status: BackendStatus,
    completed_objective_stages: tuple[ObjectiveStage, ...] = (),
) -> Diagnostics:
    return Diagnostics(
        termination_reason=termination_reason,
        backend_status=backend_status,
        elapsed_time=0.01,
        patterns_generated=0,
        iterations=0,
        completed_objective_stages=completed_objective_stages,
        lower_bound=None,
        upper_bound=None,
    )


def _optimal_solution() -> Solution:
    return Solution(
        solution_status=SolutionStatus.OPTIMAL,
        cut_patterns=(),
        objective_value=ObjectiveValue(0, 0, 0),
        diagnostics=_diagnostics(
            termination_reason=TerminationReason.COMPLETED,
            backend_status=BackendStatus.OPTIMAL,
            completed_objective_stages=(
                ObjectiveStage.STOCK_PIECE_COUNT,
                ObjectiveStage.TOTAL_LEFTOVER,
                ObjectiveStage.DISTINCT_PATTERN_COUNT,
            ),
        ),
        verification_report=VerificationReport(findings=()),
    )


def _run(
    argv: list[str],
    *,
    stdin_text: str = "",
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> tuple[int, str, str]:
    stdin = io.StringIO(stdin_text)
    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = cli.main(argv, stdin=stdin, stdout=stdout, stderr=stderr)
    return exit_code, stdout.getvalue(), stderr.getvalue()


# --- Happy path, end-to-end through the real solver ------------------------


def test_solve_from_stdin_returns_exit_0_with_a_solution_envelope() -> None:
    exit_code, stdout, stderr = _run(["solve"], stdin_text=_VALID_PROBLEM_JSON)

    assert exit_code == 0
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["format_version"] == 1
    assert payload["solution_status"] == "optimal"
    assert stdout.endswith("\n")


def test_solve_from_a_file_path_positional_argument(tmp_path) -> None:
    problem_path = tmp_path / "problem.json"
    problem_path.write_text(_VALID_PROBLEM_JSON, encoding="utf-8")

    exit_code, stdout, _ = _run(["solve", str(problem_path)])

    assert exit_code == 0
    assert json.loads(stdout)["solution_status"] == "optimal"


# --- Input errors ------------------------------------------------------


def test_missing_input_file_is_an_input_read_error(tmp_path) -> None:
    missing = tmp_path / "does-not-exist.json"

    exit_code, stdout, stderr = _run(["solve", str(missing)])

    assert exit_code == 74
    payload = json.loads(stdout)
    assert payload["error"]["code"] == "input_read_error"
    # The host path is a human diagnostic: stderr only, never the envelope.
    assert str(missing) not in payload["error"]["message"]
    assert str(missing) in stderr


def test_directory_input_path_is_an_input_read_error(tmp_path) -> None:
    exit_code, stdout, stderr = _run(["solve", str(tmp_path)])

    assert exit_code == 74
    assert json.loads(stdout)["error"]["code"] == "input_read_error"
    assert stderr.startswith("saw: error: could not read input file")


class _FailingBuffer:
    def read(self) -> bytes:
        raise OSError("device error")


class _UnreadableStdin:
    buffer = _FailingBuffer()


def test_unreadable_stdin_is_an_input_read_error() -> None:
    stdout, stderr = io.StringIO(), io.StringIO()

    exit_code = cli.main(["solve", "-"], stdin=_UnreadableStdin(), stdout=stdout, stderr=stderr)

    assert exit_code == 74
    assert json.loads(stdout.getvalue())["error"]["code"] == "input_read_error"
    assert "device error" in stderr.getvalue()


def test_missing_stdin_is_an_input_read_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # sys.stdin is None under pythonw or with file descriptor 0 closed.
    monkeypatch.setattr(sys, "stdin", None)
    stdout = io.StringIO()

    exit_code = cli.main(["solve"], stdout=stdout, stderr=io.StringIO())

    assert exit_code == 74
    assert json.loads(stdout.getvalue())["error"]["code"] == "input_read_error"


@pytest.mark.parametrize("source", ["file", "stdin"])
@pytest.mark.parametrize("content", [b"", b"   \r\n\t\n", b"\xef\xbb\xbf"])
def test_empty_input_is_invalid_json(tmp_path, source: str, content: bytes) -> None:
    exit_code, stdout, stderr = _run_bytes(tmp_path, source, content)

    assert exit_code == 2
    payload = json.loads(stdout)
    assert payload["error"]["code"] == "invalid_json"
    assert "empty" in payload["error"]["message"]
    assert "empty" in stderr


# --- Input encoding ------------------------------------------------------

_NON_ASCII_PROBLEM = {
    "format_version": 1,
    "stocks": [{"stock_id": "šipka-10", "material_id": "čelik", "length": 10, "quantity": 1}],
    "demands": [{"demand_id": "komad-ž", "material_id": "čelik", "length": 6, "quantity": 1}],
}


def _run_bytes(tmp_path, source: str, content: bytes) -> tuple[int, str, str]:
    """Run ``saw solve`` on raw bytes from a file or a byte-backed stdin.

    The stdin wrapper deliberately declares a non-UTF-8 text encoding, like a
    Windows console (cp1252), to prove the CLI decodes the underlying bytes
    itself instead of trusting the locale.
    """
    stdout, stderr = io.StringIO(), io.StringIO()
    if source == "file":
        path = tmp_path / "problem.json"
        path.write_bytes(content)
        argv, stdin = ["solve", str(path)], io.StringIO()
    else:
        argv = ["solve", "-"]
        stdin = io.TextIOWrapper(io.BytesIO(content), encoding="cp1252")
    exit_code = cli.main(argv, stdin=stdin, stdout=stdout, stderr=stderr)
    return exit_code, stdout.getvalue(), stderr.getvalue()


@pytest.mark.parametrize("source", ["file", "stdin"])
@pytest.mark.parametrize("bom", [b"", b"\xef\xbb\xbf"], ids=["no-bom", "utf8-bom"])
def test_utf8_input_is_decoded_independently_of_the_locale(
    tmp_path, source: str, bom: bytes
) -> None:
    content = bom + json.dumps(_NON_ASCII_PROBLEM, ensure_ascii=False).encode("utf-8")

    exit_code, stdout, _ = _run_bytes(tmp_path, source, content)

    assert exit_code == 0
    payload = json.loads(stdout)
    assert payload["cut_patterns"][0]["stock_id"] == "šipka-10"
    assert payload["cut_patterns"][0]["piece_counts"] == {"komad-ž": 1}


@pytest.mark.parametrize("source", ["file", "stdin"])
def test_non_utf8_input_is_invalid_json(tmp_path, source: str) -> None:
    content = json.dumps(_NON_ASCII_PROBLEM, ensure_ascii=False).encode("latin-1", "replace")
    content = content.replace(b"?", b"\xe8")  # a lone cp1252/latin-1 byte

    exit_code, stdout, stderr = _run_bytes(tmp_path, source, content)

    assert exit_code == 2
    payload = json.loads(stdout)
    assert payload["error"]["code"] == "invalid_json"
    assert "UTF-8" in payload["error"]["message"]
    assert "UTF-8" in stderr


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32"])
def test_utf16_or_utf32_input_is_invalid_json(tmp_path, encoding: str) -> None:
    # Python's "utf-16"/"utf-32" codecs write a byte order mark, like
    # Windows PowerShell 5's Out-File default.
    content = _VALID_PROBLEM_JSON.encode(encoding)

    exit_code, stdout, _ = _run_bytes(tmp_path, "file", content)

    assert exit_code == 2
    payload = json.loads(stdout)
    assert payload["error"]["code"] == "invalid_json"
    assert "byte order mark" in payload["error"]["message"]


def test_malformed_json_is_invalid_json() -> None:
    exit_code, stdout, _ = _run(["solve"], stdin_text="{not json")

    assert exit_code == 2
    assert json.loads(stdout)["error"]["code"] == "invalid_json"


def test_unsupported_format_version() -> None:
    payload = json.loads(_VALID_PROBLEM_JSON)
    payload["format_version"] = 2
    exit_code, stdout, _ = _run(["solve"], stdin_text=json.dumps(payload))

    assert exit_code == 2
    assert json.loads(stdout)["error"]["code"] == "unsupported_format_version"


def test_semantically_invalid_problem_is_invalid_problem() -> None:
    payload = json.loads(_VALID_PROBLEM_JSON)
    payload["stocks"][0]["length"] = -1
    exit_code, stdout, _ = _run(["solve"], stdin_text=json.dumps(payload))

    assert exit_code == 2
    assert json.loads(stdout)["error"]["code"] == "invalid_problem"


# --- CLI usage / option handling ----------------------------------------


def test_unknown_option_is_invalid_cli_usage() -> None:
    exit_code, stdout, _ = _run(["solve", "--bogus-flag"], stdin_text=_VALID_PROBLEM_JSON)

    assert exit_code == 2
    assert json.loads(stdout)["error"]["code"] == "invalid_cli_usage"


def test_non_numeric_limit_value_is_invalid_cli_usage() -> None:
    exit_code, stdout, _ = _run(
        ["solve", "--time-limit", "not-a-number"],
        stdin_text=_VALID_PROBLEM_JSON,
    )

    assert exit_code == 2
    assert json.loads(stdout)["error"]["code"] == "invalid_cli_usage"


def test_missing_command_is_invalid_cli_usage() -> None:
    exit_code, stdout, _ = _run([])

    assert exit_code == 2
    assert json.loads(stdout)["error"]["code"] == "invalid_cli_usage"


def test_semantically_invalid_limit_value_is_rejected_by_solve_not_the_parser() -> None:
    # 0 parses fine as an int; solve.SolveOptions is the one that rejects it
    # (docs/resource-limits.md), and the CLI only translates that ValueError.
    exit_code, stdout, _ = _run(
        ["solve", "--pattern-limit", "0"],
        stdin_text=_VALID_PROBLEM_JSON,
    )

    assert exit_code == 2
    assert json.loads(stdout)["error"]["code"] == "invalid_cli_usage"


def test_options_and_none_sentinel_are_threaded_to_solve_options_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_solve(problem, options):
        captured["options"] = options
        return _optimal_solution()

    monkeypatch.setattr(cli, "solve", fake_solve)

    exit_code, _, _ = _run(
        [
            "solve",
            "--backend",
            "custom-backend",
            "--time-limit",
            "none",
            "--pattern-limit",
            "none",
            "--problem-size-limit",
            "none",
            "--numeric-magnitude-limit",
            "none",
        ],
        stdin_text=_VALID_PROBLEM_JSON,
    )

    assert exit_code == 0
    assert captured["options"] == SolveOptions(
        backend="custom-backend",
        time_limit_seconds=None,
        pattern_limit=None,
        problem_size_limit=None,
        numeric_magnitude_limit=None,
    )


def test_numeric_limit_values_are_parsed_and_threaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_solve(problem, options):
        captured["options"] = options
        return _optimal_solution()

    monkeypatch.setattr(cli, "solve", fake_solve)

    exit_code, _, _ = _run(
        [
            "solve",
            "--time-limit",
            "5.5",
            "--pattern-limit",
            "10",
            "--problem-size-limit",
            "20",
            "--numeric-magnitude-limit",
            "30",
        ],
        stdin_text=_VALID_PROBLEM_JSON,
    )

    assert exit_code == 0
    assert captured["options"] == SolveOptions(
        backend=SolveOptions().backend,
        time_limit_seconds=5.5,
        pattern_limit=10,
        problem_size_limit=20,
        numeric_magnitude_limit=30,
    )


# --- Outcome -> exit code mapping, via a stubbed solve() -------------------


def test_infeasible_outcome_exits_3(monkeypatch: pytest.MonkeyPatch) -> None:
    solution = Solution(
        solution_status=SolutionStatus.INFEASIBLE,
        cut_patterns=(),
        objective_value=None,
        diagnostics=_diagnostics(
            termination_reason=TerminationReason.COMPLETED,
            backend_status=BackendStatus.INFEASIBLE,
        ),
    )
    monkeypatch.setattr(cli, "solve", lambda problem, options: solution)

    exit_code, stdout, _ = _run(["solve"], stdin_text=_VALID_PROBLEM_JSON)

    assert exit_code == 3
    assert json.loads(stdout)["solution_status"] == "infeasible"


def test_limit_outcome_exits_4(monkeypatch: pytest.MonkeyPatch) -> None:
    solution = Solution(
        solution_status=SolutionStatus.LIMIT,
        cut_patterns=(),
        objective_value=None,
        diagnostics=_diagnostics(
            termination_reason=TerminationReason.TIME_LIMIT,
            backend_status=BackendStatus.LIMIT_REACHED,
        ),
    )
    monkeypatch.setattr(cli, "solve", lambda problem, options: solution)

    exit_code, stdout, _ = _run(["solve"], stdin_text=_VALID_PROBLEM_JSON)

    assert exit_code == 4
    assert json.loads(stdout)["solution_status"] == "limit"


def test_unsupported_outcome_exits_5(monkeypatch: pytest.MonkeyPatch) -> None:
    solution = Solution(
        solution_status=SolutionStatus.UNSUPPORTED,
        cut_patterns=(),
        objective_value=None,
        diagnostics=_diagnostics(
            termination_reason=TerminationReason.UNSUPPORTED_PROBLEM,
            backend_status=BackendStatus.NOT_RUN,
        ),
    )
    monkeypatch.setattr(cli, "solve", lambda problem, options: solution)

    exit_code, stdout, _ = _run(["solve"], stdin_text=_VALID_PROBLEM_JSON)

    assert exit_code == 5
    assert json.loads(stdout)["solution_status"] == "unsupported"


def test_error_solution_status_exits_70_with_a_solution_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    solution = Solution(
        solution_status=SolutionStatus.ERROR,
        cut_patterns=(),
        objective_value=None,
        diagnostics=_diagnostics(
            termination_reason=TerminationReason.BACKEND_FAILURE,
            backend_status=BackendStatus.FAILED,
        ),
    )
    monkeypatch.setattr(cli, "solve", lambda problem, options: solution)

    exit_code, stdout, _ = _run(["solve"], stdin_text=_VALID_PROBLEM_JSON)

    assert exit_code == 70
    assert json.loads(stdout)["solution_status"] == "error"


def test_backend_unavailable_error_exits_6_with_an_error_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_solve(problem, options):
        raise BackendUnavailableError("CBC executable not found")

    monkeypatch.setattr(cli, "solve", fake_solve)

    exit_code, stdout, _ = _run(["solve"], stdin_text=_VALID_PROBLEM_JSON)

    assert exit_code == 6
    assert json.loads(stdout)["error"]["code"] == "backend_unavailable"


def test_internal_verification_error_exits_70_with_an_error_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_solve(problem, options):
        raise InternalVerificationError("candidate failed verification")

    monkeypatch.setattr(cli, "solve", fake_solve)

    exit_code, stdout, _ = _run(["solve"], stdin_text=_VALID_PROBLEM_JSON)

    assert exit_code == 70
    assert json.loads(stdout)["error"]["code"] == "internal_verification_error"


def test_unexpected_exception_exits_70_as_internal_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_solve(problem, options):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "solve", fake_solve)

    exit_code, stdout, stderr = _run(["solve"], stdin_text=_VALID_PROBLEM_JSON)

    assert exit_code == 70
    error = json.loads(stdout)["error"]
    assert error["code"] == "internal_error"
    # docs/formats.md: no exception class or traceback in the envelope.
    assert "RuntimeError" not in error["message"]
    assert "boom" not in error["message"]
    # The concise diagnostic goes to stderr; the traceback is opt-in.
    assert "RuntimeError: boom" in stderr
    assert "--traceback" in stderr
    assert "Traceback (most recent call last)" not in stderr


def test_keyboard_interrupt_exits_130_without_a_document_or_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def interrupted_solve(problem, options):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "solve", interrupted_solve)

    exit_code, stdout, stderr = _run(["solve"], stdin_text=_VALID_PROBLEM_JSON)

    assert exit_code == 130
    # docs/cli.md, "Interruption": outside the outcome contract, so no
    # document, and the caller's own Ctrl+C is not reported as a crash.
    assert stdout == ""
    assert stderr == "saw: interrupted\n"


def test_traceback_flag_writes_the_traceback_to_stderr_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_solve(problem, options):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "solve", fake_solve)

    exit_code, stdout, stderr = _run(["solve", "--traceback"], stdin_text=_VALID_PROBLEM_JSON)

    assert exit_code == 70
    assert stdout.count("\n") == 1
    assert json.loads(stdout)["error"]["code"] == "internal_error"
    assert "Traceback (most recent call last)" not in stdout
    assert "Traceback (most recent call last)" in stderr
    assert "fake_solve" in stderr


def test_traceback_flag_covers_handled_backend_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_solve(problem, options):
        raise InternalVerificationError("candidate failed verification")

    monkeypatch.setattr(cli, "solve", fake_solve)

    exit_code, stdout, stderr = _run(["solve", "--traceback"], stdin_text=_VALID_PROBLEM_JSON)

    assert exit_code == 70
    assert json.loads(stdout)["error"]["code"] == "internal_verification_error"
    assert "Traceback (most recent call last)" in stderr


def test_error_envelopes_are_mirrored_as_a_stderr_diagnostic() -> None:
    exit_code, stdout, stderr = _run(["solve"], stdin_text="{not json")

    assert exit_code == 2
    message = json.loads(stdout)["error"]["message"]
    assert stderr == f"saw: error: {message}\n"


# --- Output write failures --------------------------------------------


class _BrokenStream:
    def write(self, data: str) -> int:
        raise OSError("broken pipe")

    def flush(self) -> None:
        pass


def test_output_write_failure_exits_74_without_a_second_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "solve", lambda problem, options: _optimal_solution())

    stdin = io.StringIO(_VALID_PROBLEM_JSON)
    stdout = _BrokenStream()
    stderr = io.StringIO()

    exit_code = cli.main(["solve"], stdin=stdin, stdout=stdout, stderr=stderr)

    assert exit_code == 74
    assert "could not write output" in stderr.getvalue()


def test_broken_stderr_never_changes_the_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = io.StringIO()

    exit_code = cli.main(
        ["solve"],
        stdin=io.StringIO("{not json"),
        stdout=stdout,
        stderr=_BrokenStream(),
    )

    assert exit_code == 2
    assert json.loads(stdout.getvalue())["error"]["code"] == "invalid_json"


def test_both_streams_broken_still_exits_74(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "solve", lambda problem, options: _optimal_solution())

    exit_code = cli.main(
        ["solve"],
        stdin=io.StringIO(_VALID_PROBLEM_JSON),
        stdout=_BrokenStream(),
        stderr=_BrokenStream(),
    )

    assert exit_code == 74


# --- Real process: pipes, encodings, and interpreter shutdown ----------


def _saw_process(argv: list[str], **kwargs) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "saw", *argv],
        stderr=subprocess.PIPE,
        # The child must not inherit a UTF-8 override that would hide
        # locale-dependent decoding bugs.
        env={k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")},
        **kwargs,
    )


def test_process_reads_utf8_stdin_bytes_and_writes_one_document() -> None:
    content = json.dumps(_NON_ASCII_PROBLEM, ensure_ascii=False).encode("utf-8")

    process = _saw_process(["solve"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    out, err = process.communicate(content, timeout=120)

    assert process.returncode == 0, err
    assert out.endswith(b"\n")
    assert out.count(b"\n") == 1
    payload = json.loads(out)
    assert payload["cut_patterns"][0]["stock_id"] == "šipka-10"
    assert err == b""


def test_process_broken_stdout_pipe_exits_74_without_shutdown_noise(tmp_path) -> None:
    problem_path = tmp_path / "problem.json"
    problem_path.write_text(_VALID_PROBLEM_JSON, encoding="utf-8")
    # A pipe whose read end is closed before the child starts: the reader
    # has "exited early", so the child's first stdout write fails
    # deterministically (EPIPE on POSIX, EINVAL on Windows).
    read_end, write_end = os.pipe()
    os.close(read_end)
    try:
        process = _saw_process(
            ["solve", str(problem_path)], stdin=subprocess.DEVNULL, stdout=write_end
        )
    finally:
        os.close(write_end)
    _, err = process.communicate(timeout=120)

    assert process.returncode == 74, err
    stderr = err.decode("utf-8", "replace")
    assert "could not write output" in stderr
    # Without redirecting the fd, the interpreter's shutdown flush would
    # print this and force exit code 120.
    assert "Exception ignored" not in stderr
    assert "Traceback" not in stderr


# --- Human-oriented output, outside the JSON contract -----------------


def test_version_flag_reports_the_installed_version(capsys: pytest.CaptureFixture) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])

    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "saw" in out


def test_help_documents_input_modes_output_behavior_and_limits(
    capsys: pytest.CaptureFixture,
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["solve", "--help"])

    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "standard input" in out
    assert "Standard output" in out
    assert "--time-limit" in out
    assert "--pattern-limit" in out


def test_an_integer_past_the_digit_limit_is_named_as_too_long() -> None:
    exit_code, stdout, _ = _run(
        ["solve", "--pattern-limit", "9" * 5000],
        stdin_text=_VALID_PROBLEM_JSON,
    )

    assert exit_code == 2
    message = json.loads(stdout)["error"]["message"]
    assert "too many digits" in message
    # The argument is echoed back only in shortened form.
    assert len(message) < 200


class _TerminalStdin(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_an_interactive_stdin_gets_a_hint_on_stderr() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.main(
        ["solve"],
        stdin=_TerminalStdin(_VALID_PROBLEM_JSON),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert "reading the problem from standard input" in stderr.getvalue()
    assert json.loads(stdout.getvalue())["solution_status"] == "optimal"


def test_a_piped_stdin_gets_no_hint() -> None:
    exit_code, _, stderr = _run(["solve"], stdin_text=_VALID_PROBLEM_JSON)

    assert exit_code == 0
    assert stderr == ""
