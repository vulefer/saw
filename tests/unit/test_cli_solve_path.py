"""The CLI solves only through the public, verified ``saw.solve`` path.

Unlike ``test_cli.py``, which stubs ``cli.solve`` to exercise the stream and
exit-code contract, these tests keep the real ``solve()`` in place and swap
only the backend registry underneath it, so every assertion is about what a
plan must pass through before the CLI may emit it.
"""

from __future__ import annotations

import ast
import dataclasses
import io
import json
import math
import subprocess
import sys
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path

import pytest

from saw import cli, json_from_solution, problem_from_json, solve
from saw.backends.base import (
    BackendAvailability,
    BackendCapabilities,
    BackendOptions,
    BackendSupport,
    UnverifiedBackendResult,
    UnverifiedCandidate,
)
from saw.models import (
    BackendStatus,
    CutPattern,
    Diagnostics,
    ObjectiveStage,
    ObjectiveValue,
    Problem,
    TerminationReason,
)
from saw.patterns import Pattern
from saw.solve import SolveOptions

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOCUMENTED_PROBLEM = _REPO_ROOT / "examples" / "json" / "problem-v1-valid.json"
_DOCUMENTED_SOLUTION = _REPO_ROOT / "examples" / "json" / "solution-v1-optimal.json"

# Matches examples/json/problem-v1-valid.json: one 10-long stock (quantity
# 2) and demands of 6 and 4, so a single stock piece cut 6+4 is optimal.
_VALID_PATTERN = CutPattern(
    Pattern("stock-10", {"demand-6": 1, "demand-4": 1}), repetitions=1,
)
_VALID_OBJECTIVE = ObjectiveValue(1, 0, 1)


def _diagnostics(
    status: BackendStatus,
    reason: TerminationReason = TerminationReason.COMPLETED,
) -> Diagnostics:
    return Diagnostics(
        termination_reason=reason,
        backend_status=status,
        elapsed_time=0.0,
        patterns_generated=1,
        iterations=1,
        completed_objective_stages=tuple(ObjectiveStage),
        lower_bound=None,
        upper_bound=None,
    )


@dataclass
class RecordingBackend:
    result: UnverifiedBackendResult | None = None
    raises: Exception | None = None
    received_options: BackendOptions | None = None
    calls: list[str] = field(default_factory=list)

    @property
    def backend_id(self) -> str:
        return "exact_enumerated"

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            proves_optimality=True,
            proves_infeasibility=True,
            supports_time_limit=True,
            supports_pattern_limit=True,
            objective_stages=frozenset(ObjectiveStage),
        )

    def check_availability(self) -> BackendAvailability:
        self.calls.append("check_availability")
        return BackendAvailability(available=True)

    def check_support(
        self, problem: Problem, options: BackendOptions,
    ) -> BackendSupport:
        self.calls.append("check_support")
        return BackendSupport(supported=True)

    def solve(
        self, problem: Problem, options: BackendOptions,
    ) -> UnverifiedBackendResult:
        self.calls.append("solve")
        self.received_options = options
        if self.raises is not None:
            raise self.raises
        assert self.result is not None
        return self.result


def _install_backend(
    monkeypatch: pytest.MonkeyPatch, backend: RecordingBackend,
) -> None:
    solve_module = import_module("saw.solve")
    monkeypatch.setattr(solve_module, "_BACKENDS", {"exact_enumerated": backend})


def _run(argv: list[str], stdin_text: str) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = cli.main(
        argv, stdin=io.StringIO(stdin_text), stdout=stdout, stderr=stderr,
    )
    return exit_code, stdout.getvalue(), stderr.getvalue()


def _without_elapsed_time(document: dict) -> dict:
    # elapsed_time is the only field that legitimately differs between two
    # runs of the same request (docs/formats.md, "Diagnostics").
    document = json.loads(json.dumps(document))
    document["diagnostics"].pop("elapsed_time")
    return document


# --- The CLI does not call optimization adapters directly ------------------


def test_cli_module_imports_no_backend_and_only_the_public_solve_path() -> None:
    tree = ast.parse(Path(cli.__file__).read_text(encoding="utf-8"))
    saw_imports: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("saw"), alias.name
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("saw"):
            saw_imports.setdefault(node.module, set()).update(
                alias.name for alias in node.names
            )

    assert not any(module.startswith("saw.backends") for module in saw_imports)
    assert saw_imports["saw.solve"] == {
        "InvalidSolveOptionsError", "SolveOptions", "solve",
    }
    assert set(saw_imports) <= {
        "saw.exceptions", "saw.models", "saw.serialization", "saw.solve",
    }
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert "_BACKENDS" not in names | attributes


def test_cli_reaches_the_backend_only_through_solve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = RecordingBackend(
        result=UnverifiedBackendResult(
            candidate=UnverifiedCandidate((_VALID_PATTERN,), _VALID_OBJECTIVE),
            diagnostics=_diagnostics(BackendStatus.OPTIMAL),
        ),
    )
    _install_backend(monkeypatch, backend)

    exit_code, stdout, _ = _run(["solve"], _DOCUMENTED_PROBLEM.read_text())

    assert exit_code == 0
    # solve()'s own call order, including the availability and support checks
    # the CLI would skip if it drove the adapter itself.
    assert backend.calls == ["check_availability", "check_support", "solve"]
    assert json.loads(stdout)["verification_report"]["result"] == "valid"


# --- Every emitted plan has passed the verification gate --------------------


@pytest.mark.parametrize(
    "status", [BackendStatus.OPTIMAL, BackendStatus.FEASIBLE, BackendStatus.LIMIT_REACHED],
)
def test_a_candidate_failing_verification_is_never_emitted(
    monkeypatch: pytest.MonkeyPatch, status: BackendStatus,
) -> None:
    # Cuts 6+6 from a 10-long stock: over capacity and the wrong demands.
    corrupted = CutPattern(Pattern("stock-10", {"demand-6": 2}), repetitions=1)
    _install_backend(
        monkeypatch,
        RecordingBackend(
            result=UnverifiedBackendResult(
                candidate=UnverifiedCandidate((corrupted,), _VALID_OBJECTIVE),
                diagnostics=_diagnostics(status),
            ),
        ),
    )

    exit_code, stdout, _ = _run(["solve"], _DOCUMENTED_PROBLEM.read_text())

    assert exit_code == 70
    document = json.loads(stdout)
    assert document["error"]["code"] == "internal_verification_error"
    assert "cut_patterns" not in document


def test_a_limit_incumbent_is_emitted_only_with_its_verification_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_backend(
        monkeypatch,
        RecordingBackend(
            result=UnverifiedBackendResult(
                candidate=UnverifiedCandidate((_VALID_PATTERN,), _VALID_OBJECTIVE),
                diagnostics=_diagnostics(
                    BackendStatus.LIMIT_REACHED, TerminationReason.TIME_LIMIT,
                ),
            ),
        ),
    )

    exit_code, stdout, _ = _run(["solve"], _DOCUMENTED_PROBLEM.read_text())

    assert exit_code == 4
    document = json.loads(stdout)
    assert document["solution_status"] == "limit"
    assert document["cut_patterns"]
    assert document["verification_report"] == {"result": "valid", "findings": []}


def test_a_value_error_from_a_backend_is_an_internal_error_not_cli_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Only InvalidSolveOptionsError means "bad invocation"; a ValueError from
    # inside solving is a backend/internal failure (docs/cli.md, "Outcome
    # precedence").
    _install_backend(
        monkeypatch, RecordingBackend(raises=ValueError("backend bug")),
    )

    exit_code, stdout, stderr = _run(["solve"], _DOCUMENTED_PROBLEM.read_text())

    assert exit_code == 70
    assert json.loads(stdout)["error"]["code"] == "internal_error"
    assert "ValueError: backend bug" in stderr


# --- CLI limit flags map explicitly into validated SolveOptions -------------


def test_every_solve_option_has_exactly_one_cli_flag() -> None:
    limit_fields = [limit.field for limit in cli._LIMIT_FLAGS]
    flags = [limit.flag for limit in cli._LIMIT_FLAGS]

    assert len(set(limit_fields)) == len(limit_fields)
    assert len(set(flags)) == len(flags)
    assert {"backend", *limit_fields} == {
        option.name for option in dataclasses.fields(SolveOptions)
    }


def test_omitted_flags_are_exactly_the_python_defaults() -> None:
    args = cli.build_parser().parse_args(["solve"])

    assert cli._solve_options_from_args(args) == SolveOptions()


@pytest.mark.parametrize("limit", cli._LIMIT_FLAGS, ids=lambda limit: limit.flag)
@pytest.mark.parametrize("raw, expected", [("7", 7), ("none", None)])
def test_each_limit_flag_sets_only_its_own_solve_options_field(
    limit: cli._LimitFlag, raw: str, expected: object,
) -> None:
    args = cli.build_parser().parse_args(["solve", limit.flag, raw])

    assert cli._solve_options_from_args(args) == dataclasses.replace(
        SolveOptions(), **{limit.field: expected},
    )


def test_limit_flags_reach_the_backend_as_validated_backend_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = RecordingBackend(
        result=UnverifiedBackendResult(
            candidate=UnverifiedCandidate((_VALID_PATTERN,), _VALID_OBJECTIVE),
            diagnostics=_diagnostics(BackendStatus.OPTIMAL),
        ),
    )
    _install_backend(monkeypatch, backend)

    exit_code, _, _ = _run(
        ["solve", "--time-limit", "12.5", "--pattern-limit", "321"],
        _DOCUMENTED_PROBLEM.read_text(),
    )

    assert exit_code == 0
    assert backend.received_options is not None
    assert backend.received_options.time_limit_seconds == 12.5
    assert backend.received_options.pattern_limit == 321


@pytest.mark.parametrize(
    "argv, field_name",
    [
        (["--time-limit", "0"], "time_limit_seconds"),
        (["--time-limit", "-1"], "time_limit_seconds"),
        (["--time-limit", "nan"], "time_limit_seconds"),
        (["--time-limit", "inf"], "time_limit_seconds"),
        (["--pattern-limit", "0"], "pattern_limit"),
        (["--problem-size-limit", "-3"], "problem_size_limit"),
        (["--numeric-magnitude-limit", "0"], "numeric_magnitude_limit"),
        (["--backend", "missing"], "backend"),
    ],
)
def test_invalid_option_values_are_rejected_by_solve_options_validation(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], field_name: str,
) -> None:
    backend = RecordingBackend()
    _install_backend(monkeypatch, backend)

    exit_code, stdout, _ = _run(["solve", *argv], _DOCUMENTED_PROBLEM.read_text())

    assert exit_code == 2
    error = json.loads(stdout)["error"]
    assert error["code"] == "invalid_cli_usage"
    assert field_name in error["message"].lower()
    assert backend.calls == []
    # The same value is rejected with the same message from Python.
    problem = problem_from_json(_DOCUMENTED_PROBLEM.read_text())
    options = cli._solve_options_from_args(
        cli.build_parser().parse_args(["solve", *argv]),
    )
    with pytest.raises(ValueError) as raised:
        solve(problem, options)
    assert str(raised.value) == error["message"]


@pytest.mark.parametrize("flag", ["--pattern-limit", "--time-limit"])
def test_non_numeric_limit_text_names_the_expected_value(flag: str) -> None:
    exit_code, stdout, _ = _run(["solve", flag, "lots"], "")

    assert exit_code == 2
    message = json.loads(stdout)["error"]["message"]
    assert flag in message
    assert "'lots'" in message
    assert "'none'" in message


# --- Python and the CLI agree on documented requests -----------------------


def _python_document(problem_text: str, options: SolveOptions) -> dict:
    solution = solve(problem_from_json(problem_text), options)
    return json.loads(json_from_solution(solution))


@pytest.mark.parametrize(
    "argv, options",
    [
        ([], SolveOptions()),
        (["--time-limit", "none", "--pattern-limit", "none"],
         SolveOptions(time_limit_seconds=None, pattern_limit=None)),
        (["--problem-size-limit", "1"], SolveOptions(problem_size_limit=1)),
        (["--numeric-magnitude-limit", "5"], SolveOptions(numeric_magnitude_limit=5)),
    ],
)
def test_documented_request_has_the_same_result_through_python_and_the_cli(
    argv: list[str], options: SolveOptions,
) -> None:
    problem_text = _DOCUMENTED_PROBLEM.read_text(encoding="utf-8")

    exit_code, stdout, _ = _run(["solve", str(_DOCUMENTED_PROBLEM), *argv], "")

    assert _without_elapsed_time(json.loads(stdout)) == _without_elapsed_time(
        _python_document(problem_text, options)
    )
    assert exit_code == cli._SOLUTION_STATUS_EXIT_CODES[
        solve(problem_from_json(problem_text), options).solution_status
    ]


def test_documented_request_matches_the_documented_solution_in_a_real_process() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "saw", "solve", str(_DOCUMENTED_PROBLEM)],
        capture_output=True,
        timeout=120,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    cli_document = json.loads(completed.stdout.decode("utf-8"))
    python_document = _python_document(
        _DOCUMENTED_PROBLEM.read_text(encoding="utf-8"), SolveOptions(),
    )
    documented = json.loads(_DOCUMENTED_SOLUTION.read_text(encoding="utf-8"))

    assert _without_elapsed_time(cli_document) == _without_elapsed_time(python_document)
    # The documented example is the same plan with the same proof, even
    # though its diagnostic counters are illustrative.
    for key in ("format_version", "solution_status", "cut_patterns",
                "objective_value", "verification_report"):
        assert cli_document[key] == documented[key], key
    assert math.isfinite(cli_document["diagnostics"]["elapsed_time"])
