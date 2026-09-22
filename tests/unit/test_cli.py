from __future__ import annotations

import io
import json

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
        "stocks": [
            {"stock_id": "stock-10", "material_id": "steel", "length": 10, "quantity": 1}
        ],
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
    assert stderr == ""


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
        ["solve", "--time-limit", "not-a-number"], stdin_text=_VALID_PROBLEM_JSON,
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
        ["solve", "--pattern-limit", "0"], stdin_text=_VALID_PROBLEM_JSON,
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
            "--backend", "custom-backend",
            "--time-limit", "none",
            "--pattern-limit", "none",
            "--problem-size-limit", "none",
            "--numeric-magnitude-limit", "none",
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
            "--time-limit", "5.5",
            "--pattern-limit", "10",
            "--problem-size-limit", "20",
            "--numeric-magnitude-limit", "30",
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

    exit_code, stdout, _ = _run(["solve"], stdin_text=_VALID_PROBLEM_JSON)

    assert exit_code == 70
    assert json.loads(stdout)["error"]["code"] == "internal_error"


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
