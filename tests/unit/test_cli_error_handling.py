"""Every expected failure mode maps to the documented CLI outcome contract.

One place that walks the whole ``docs/cli.md`` exit-code table: invalid
JSON, invalid problems, infeasibility, an unavailable CBC, resource limits,
unsupported requests, and internal failures. Like ``test_cli_solve_path.py``,
these tests keep the real ``solve()`` and swap at most the backend registry
(or the PuLP/CBC solver factory) underneath it, so each outcome is produced
the way a real run would produce it.

Every case also checks the stream invariants: standard output holds exactly
one newline-terminated JSON document, and no traceback ever reaches it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module
import io
import json
from pathlib import Path

import pytest

from saw import cli
from saw.backends.base import (
    BackendAvailability,
    BackendCapabilities,
    BackendOptions,
    BackendSupport,
    UnverifiedBackendResult,
    UnverifiedCandidate,
)
from saw.exceptions import BackendUnavailableError
from saw.models import (
    BackendStatus,
    CutPattern,
    Diagnostics,
    ObjectiveStage,
    ObjectiveValue,
    Problem,
    SolutionStatus,
    TerminationReason,
)
from saw.patterns import Pattern

_REPO_ROOT = Path(__file__).resolve().parents[2]
_VALID_PROBLEM = (_REPO_ROOT / "examples" / "json" / "problem-v1-valid.json").read_text(
    encoding="utf-8"
)

# Three 6-long pieces cannot come from two 10-long stocks: infeasible.
_INFEASIBLE_PROBLEM = json.dumps(
    {
        "format_version": 1,
        "stocks": [{"stock_id": "stock-10", "material_id": "steel", "length": 10, "quantity": 2}],
        "demands": [{"demand_id": "demand-6", "material_id": "steel", "length": 6, "quantity": 3}],
    }
)

_VALID_PATTERN = CutPattern(
    Pattern("stock-10", {"demand-6": 1, "demand-4": 1}),
    repetitions=1,
)
# Cuts 6+6 from a 10-long stock: over capacity and the wrong demands.
_CORRUPTED_PATTERN = CutPattern(Pattern("stock-10", {"demand-6": 2}), repetitions=1)
_VALID_OBJECTIVE = ObjectiveValue(1, 0, 1)

_TRACEBACK_HEADER = "Traceback (most recent call last)"


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
        completed_objective_stages=(),
        lower_bound=None,
        upper_bound=None,
    )


@dataclass
class FakeBackend:
    """A registry stand-in whose every step can report or raise anything."""

    result: UnverifiedBackendResult | None = None
    raises: BaseException | None = None
    availability: BackendAvailability = field(
        default_factory=lambda: BackendAvailability(available=True),
    )
    availability_raises: Exception | None = None
    supported: bool = True
    prints: str | None = None

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
        if self.availability_raises is not None:
            raise self.availability_raises
        return self.availability

    def check_support(
        self,
        problem: Problem,
        options: BackendOptions,
    ) -> BackendSupport:
        return BackendSupport(
            supported=self.supported,
            reason=None if self.supported else "not supported by this backend",
        )

    def solve(
        self,
        problem: Problem,
        options: BackendOptions,
    ) -> UnverifiedBackendResult:
        if self.prints is not None:
            print(self.prints)
        if self.raises is not None:
            raise self.raises
        assert self.result is not None
        return self.result


def _install_backend(monkeypatch: pytest.MonkeyPatch, backend: FakeBackend) -> None:
    solve_module = import_module("saw.solve")
    monkeypatch.setattr(solve_module, "_BACKENDS", {"exact_enumerated": backend})


def _run(argv: list[str], stdin_text: str) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = cli.main(
        argv,
        stdin=io.StringIO(stdin_text),
        stdout=stdout,
        stderr=stderr,
    )
    return exit_code, stdout.getvalue(), stderr.getvalue()


def _single_document(stdout: str) -> dict:
    """Assert the docs/cli.md stream invariants and return the document."""
    assert stdout.endswith("\n")
    assert stdout.count("\n") == 1
    assert _TRACEBACK_HEADER not in stdout
    return json.loads(stdout)


def _assert_error_envelope(stdout: str, code: str) -> dict:
    document = _single_document(stdout)
    assert set(document) == {"format_version", "error"}
    assert set(document["error"]) == {"code", "message"}
    assert document["error"]["code"] == code
    return document


# --- Invalid input: exit 2 --------------------------------------------------


@pytest.mark.parametrize(
    ("stdin_text", "code"),
    [
        ("{not json", "invalid_json"),
        ('{"format_version": 1, "format_version": 1}', "invalid_json"),
        ('{"format_version": NaN}', "invalid_json"),
        # Deeply nested input exhausts the parser's stack; it is still
        # unacceptable input, not an internal failure.
        ("[" * 100_000, "invalid_json"),
        ("[" * 100_000 + "]" * 100_000, "invalid_json"),
        (
            _VALID_PROBLEM.replace('"format_version": 1', '"format_version": 2'),
            "unsupported_format_version",
        ),
        ('{"format_version": 1}', "invalid_problem"),
        (
            _VALID_PROBLEM.replace('"quantity": 1', '"quantity": 0', 1),
            "invalid_problem",
        ),
    ],
    ids=[
        "malformed",
        "duplicate-member",
        "nan-constant",
        "unclosed-deep-nesting",
        "closed-deep-nesting",
        "format-version-2",
        "missing-members",
        "zero-quantity",
    ],
)
def test_invalid_input_exits_2_with_its_error_code(stdin_text: str, code: str) -> None:
    exit_code, stdout, stderr = _run(["solve", "--traceback"], stdin_text)

    assert exit_code == 2
    _assert_error_envelope(stdout, code)
    # A caller's input error is not an internal failure: no traceback even
    # when one was requested.
    assert _TRACEBACK_HEADER not in stderr


def test_invalid_input_wins_over_an_unavailable_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_backend(
        monkeypatch,
        FakeBackend(availability=BackendAvailability(available=False, reason="no CBC")),
    )

    exit_code, stdout, _ = _run(["solve"], '{"format_version": 1}')

    assert exit_code == 2
    _assert_error_envelope(stdout, "invalid_problem")


@pytest.mark.parametrize(
    "argv",
    [
        ["solve", "--no-such-option"],
        ["solve", "--time-limit", "soon"],
        ["solve", "--time-limit", "0"],
        ["solve", "--pattern-limit", "-1"],
        ["solve", "--backend", "no_such_backend"],
    ],
)
def test_invalid_invocation_exits_2_as_invalid_cli_usage(argv: list[str]) -> None:
    exit_code, stdout, _ = _run(argv, _VALID_PROBLEM)

    assert exit_code == 2
    _assert_error_envelope(stdout, "invalid_cli_usage")


# --- Normal non-success outcomes: exits 3, 4, 5 -----------------------------


def test_proved_infeasibility_exits_3_with_a_solution_envelope() -> None:
    exit_code, stdout, _ = _run(["solve"], _INFEASIBLE_PROBLEM)

    assert exit_code == 3
    document = _single_document(stdout)
    assert document["solution_status"] == "infeasible"
    assert "cut_patterns" not in document


@pytest.mark.parametrize(
    "limit_flag",
    ["--problem-size-limit", "--numeric-magnitude-limit"],
)
def test_a_preflight_limit_exits_4_without_a_plan(limit_flag: str) -> None:
    exit_code, stdout, _ = _run(["solve", limit_flag, "1"], _VALID_PROBLEM)

    assert exit_code == 4
    document = _single_document(stdout)
    assert document["solution_status"] == "limit"
    assert document["diagnostics"]["termination_reason"] == "size_limit"
    assert "cut_patterns" not in document


@pytest.mark.parametrize(
    "reason",
    [TerminationReason.TIME_LIMIT, TerminationReason.PATTERN_LIMIT],
)
def test_a_backend_limit_without_an_incumbent_exits_4(
    monkeypatch: pytest.MonkeyPatch,
    reason: TerminationReason,
) -> None:
    _install_backend(
        monkeypatch,
        FakeBackend(
            result=UnverifiedBackendResult(
                candidate=None,
                diagnostics=_diagnostics(BackendStatus.LIMIT_REACHED, reason),
            ),
        ),
    )

    exit_code, stdout, _ = _run(["solve"], _VALID_PROBLEM)

    assert exit_code == 4
    document = _single_document(stdout)
    assert document["solution_status"] == "limit"
    assert document["diagnostics"]["termination_reason"] == reason.value


def test_an_unsupported_request_exits_5(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_backend(monkeypatch, FakeBackend(supported=False))

    exit_code, stdout, _ = _run(["solve"], _VALID_PROBLEM)

    assert exit_code == 5
    document = _single_document(stdout)
    assert document["solution_status"] == "unsupported"
    assert "cut_patterns" not in document


# --- Unavailable CBC: exit 6 ------------------------------------------------


class _MissingCbc:
    def available(self) -> bool:
        return False


def test_an_unavailable_cbc_exits_6_through_the_real_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The real exact_enumerated backend and PuLP adapter; only the CBC
    # executable lookup is made to fail.
    monkeypatch.setattr(
        "saw.backends.pulp_backend._new_cbc_solver",
        lambda options: _MissingCbc(),
    )

    exit_code, stdout, stderr = _run(["solve"], _VALID_PROBLEM)

    assert exit_code == 6
    document = _assert_error_envelope(stdout, "backend_unavailable")
    assert "CBC" in document["error"]["message"]
    assert "CBC" in stderr


def test_cbc_disappearing_mid_solve_still_exits_6(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_backend(
        monkeypatch,
        FakeBackend(raises=BackendUnavailableError("no executable CBC solver")),
    )

    exit_code, stdout, _ = _run(["solve"], _VALID_PROBLEM)

    assert exit_code == 6
    _assert_error_envelope(stdout, "backend_unavailable")


# --- Internal failures: exit 70, never a false mathematical claim -----------


def _backend_result(status: BackendStatus, *, corrupted: bool = False, candidate: bool = True):
    pattern = _CORRUPTED_PATTERN if corrupted else _VALID_PATTERN
    return UnverifiedBackendResult(
        candidate=(UnverifiedCandidate((pattern,), _VALID_OBJECTIVE) if candidate else None),
        diagnostics=_diagnostics(status),
    )


_INTERNAL_FAILURES: dict[str, FakeBackend] = {
    "backend raises": FakeBackend(raises=RuntimeError("boom")),
    "backend raises ValueError": FakeBackend(raises=ValueError("bad index")),
    "availability check raises": FakeBackend(availability_raises=OSError("probe crashed")),
    "optimal without a candidate": FakeBackend(
        result=_backend_result(BackendStatus.OPTIMAL, candidate=False),
    ),
    "optimal with a corrupted candidate": FakeBackend(
        result=_backend_result(BackendStatus.OPTIMAL, corrupted=True),
    ),
    "feasible with a corrupted candidate": FakeBackend(
        result=_backend_result(BackendStatus.FEASIBLE, corrupted=True),
    ),
    "limit with a corrupted incumbent": FakeBackend(
        result=_backend_result(BackendStatus.LIMIT_REACHED, corrupted=True),
    ),
    "structured backend failure": FakeBackend(
        result=_backend_result(BackendStatus.FAILED, candidate=False),
    ),
    "backend status unknown": FakeBackend(
        result=_backend_result(BackendStatus.UNKNOWN, candidate=False),
    ),
    # A failed backend must not smuggle a plan out alongside its failure.
    "structured failure with a stray candidate": FakeBackend(
        result=_backend_result(BackendStatus.FAILED),
    ),
}


@pytest.mark.parametrize("traceback_flag", [False, True], ids=["default", "traceback"])
@pytest.mark.parametrize("backend", _INTERNAL_FAILURES.values(), ids=_INTERNAL_FAILURES)
def test_internal_failures_exit_70_without_a_false_claim(
    monkeypatch: pytest.MonkeyPatch,
    backend: FakeBackend,
    traceback_flag: bool,
) -> None:
    _install_backend(monkeypatch, backend)
    argv = ["solve", "--traceback"] if traceback_flag else ["solve"]

    exit_code, stdout, stderr = _run(argv, _VALID_PROBLEM)

    assert exit_code == 70
    document = _single_document(stdout)
    if "error" in document:
        assert document["error"]["code"] in {
            "internal_error",
            "internal_verification_error",
        }
    else:
        # docs/cli.md: a structured backend error is a solution envelope
        # with solution_status "error" -- and nothing that looks like a plan.
        assert document["solution_status"] == "error"
        assert "cut_patterns" not in document
        assert "objective_value" not in document
    # Exit 70 never co-occurs with an optimality or infeasibility claim.
    assert document.get("solution_status") not in {"optimal", "feasible", "infeasible"}
    # Tracebacks are opt-in and only ever reach standard error.
    raised = backend.raises is not None or backend.availability_raises is not None
    if raised or document.get("error", {}).get("code") == "internal_verification_error":
        assert (_TRACEBACK_HEADER in stderr) is traceback_flag


def test_internal_error_envelope_hides_exception_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_backend(
        monkeypatch,
        FakeBackend(raises=RuntimeError("secret C:\\Users\\me\\model.lp")),
    )

    exit_code, stdout, stderr = _run(["solve"], _VALID_PROBLEM)

    assert exit_code == 70
    message = _assert_error_envelope(stdout, "internal_error")["error"]["message"]
    assert "RuntimeError" not in message
    assert "secret" not in message
    assert "RuntimeError: secret" in stderr
    assert "--traceback" in stderr


def test_an_unmapped_solution_status_is_an_internal_error_not_a_second_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_backend(
        monkeypatch,
        FakeBackend(result=_backend_result(BackendStatus.OPTIMAL)),
    )
    exit_codes = dict(cli._SOLUTION_STATUS_EXIT_CODES)
    del exit_codes[SolutionStatus.OPTIMAL]
    monkeypatch.setattr(cli, "_SOLUTION_STATUS_EXIT_CODES", exit_codes)

    exit_code, stdout, _ = _run(["solve"], _VALID_PROBLEM)

    assert exit_code == 70
    document = _assert_error_envelope(stdout, "internal_error")
    assert "solution_status" not in document


# --- Stray output from solving never reaches standard output ----------------


def test_stray_backend_prints_go_to_stderr_not_the_json_document(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    _install_backend(
        monkeypatch,
        FakeBackend(
            result=_backend_result(BackendStatus.OPTIMAL),
            prints="Welcome to the CBC MILP Solver",
        ),
    )

    exit_code, stdout, stderr = _run(["solve"], _VALID_PROBLEM)

    assert exit_code == 0
    assert _single_document(stdout)["solution_status"] == "optimal"
    assert "Welcome to the CBC MILP Solver" in stderr
    # Nor does it leak to the process's real standard output.
    assert "CBC" not in capsys.readouterr().out
