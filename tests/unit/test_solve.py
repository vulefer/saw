from __future__ import annotations

from dataclasses import dataclass, field, replace
from importlib import import_module
from unittest.mock import Mock

import pytest

from saw import Demand, Problem, SolveOptions, Stock, solve
from saw.backends.base import (
    BackendAvailability,
    BackendCapabilities,
    BackendOptions,
    BackendSupport,
    UnverifiedBackendResult,
    UnverifiedCandidate,
)
from saw.exceptions import (
    BackendUnavailableError,
    InternalVerificationError,
    ValidationError,
)
from saw.models import (
    BackendStatus,
    Diagnostics,
    ObjectiveStage,
    ObjectiveValue,
    SolutionStatus,
    TerminationReason,
)


def _diagnostics(status: BackendStatus) -> Diagnostics:
    return Diagnostics(
        termination_reason=TerminationReason.COMPLETED,
        backend_status=status,
        elapsed_time=0.25,
        patterns_generated=3,
        iterations=1,
        completed_objective_stages=tuple(ObjectiveStage),
        lower_bound=0,
        upper_bound=0,
    )


@dataclass
class RecordingBackend:
    result: UnverifiedBackendResult
    selected_id: str = "recording"
    availability: BackendAvailability = field(
        default_factory=lambda: BackendAvailability(available=True)
    )
    support: BackendSupport = field(
        default_factory=lambda: BackendSupport(supported=True)
    )
    calls: list[str] = field(default_factory=list)
    received_problem: Problem | None = None
    received_options: BackendOptions | None = None

    @property
    def backend_id(self) -> str:
        return self.selected_id

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
        return self.availability

    def check_support(
        self,
        problem: Problem,
        options: BackendOptions,
    ) -> BackendSupport:
        self.calls.append("check_support")
        self.received_problem = problem
        self.received_options = options
        return self.support

    def solve(
        self,
        problem: Problem,
        options: BackendOptions,
    ) -> UnverifiedBackendResult:
        self.calls.append("solve")
        self.received_problem = problem
        self.received_options = options
        return self.result


def _empty_candidate_result(
    status: BackendStatus = BackendStatus.OPTIMAL,
) -> UnverifiedBackendResult:
    return UnverifiedBackendResult(
        candidate=UnverifiedCandidate(
            cut_patterns=(),
            objective_value=ObjectiveValue(0, 0, 0),
        ),
        diagnostics=_diagnostics(status),
    )


def _install_backend(
    monkeypatch: pytest.MonkeyPatch,
    backend_id: str,
    backend: RecordingBackend,
) -> None:
    solve_module = import_module("saw.solve")
    monkeypatch.setattr(solve_module, "_BACKENDS", {backend_id: backend})


def test_problem_validation_runs_before_backend_selection() -> None:
    invalid_problem = Problem(
        stocks=(Stock("stock", "steel", 0, 1),),
        demands=(),
    )

    with pytest.raises(ValidationError):
        solve(invalid_problem, SolveOptions(backend="missing"))


def test_default_backend_is_selected_through_the_strategy_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    problem = Problem(stocks=(), demands=())
    backend = RecordingBackend(_empty_candidate_result())
    _install_backend(monkeypatch, "exact_enumerated", backend)

    result = solve(problem)

    assert result.solution_status is SolutionStatus.OPTIMAL
    assert backend.calls == ["check_availability", "check_support", "solve"]
    assert backend.received_problem is problem
    # docs/resource-limits.md's v0.2.0 defaults: omitting SolveOptions
    # entirely must still apply the safe 30-second / 50,000-pattern
    # defaults, not reach the backend unlimited.
    assert backend.received_options == BackendOptions(
        time_limit_seconds=30.0,
        pattern_limit=50_000,
    )


def test_backend_can_be_selected_explicitly_and_receives_resource_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    problem = Problem(stocks=(), demands=())
    backend = RecordingBackend(
        _empty_candidate_result(),
        selected_id="alternative",
    )
    _install_backend(monkeypatch, "alternative", backend)

    solve(
        problem,
        SolveOptions(
            backend="alternative",
            time_limit_seconds=2.5,
            pattern_limit=100,
        ),
    )

    assert backend.received_options == BackendOptions(
        time_limit_seconds=2.5,
        pattern_limit=100,
    )


def test_backend_diagnostics_are_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    problem = Problem(stocks=(), demands=())
    raw_result = _empty_candidate_result()
    backend = RecordingBackend(raw_result)
    _install_backend(monkeypatch, "exact_enumerated", backend)

    result = solve(problem)

    # solve() stamps backend_id/backend_version onto a new Diagnostics
    # object (see docs/formats.md, "Python-only diagnostics fields"), so
    # every other field is preserved by value, not by identity.
    assert result.diagnostics == replace(
        raw_result.diagnostics,
        backend_id=backend.backend_id,
        backend_version=backend.availability.backend_version,
    )
    assert result.cut_patterns == raw_result.candidate.cut_patterns
    assert result.objective_value is raw_result.candidate.objective_value


@pytest.mark.parametrize(
    ("backend_status", "solution_status", "has_candidate"),
    [
        (BackendStatus.OPTIMAL, SolutionStatus.OPTIMAL, True),
        (BackendStatus.FEASIBLE, SolutionStatus.FEASIBLE, True),
        (BackendStatus.INFEASIBLE, SolutionStatus.INFEASIBLE, False),
        (BackendStatus.LIMIT_REACHED, SolutionStatus.LIMIT, False),
        (BackendStatus.FAILED, SolutionStatus.ERROR, False),
        (BackendStatus.UNKNOWN, SolutionStatus.ERROR, False),
    ],
)
def test_expected_backend_outcomes_return_structured_solutions(
    monkeypatch: pytest.MonkeyPatch,
    backend_status: BackendStatus,
    solution_status: SolutionStatus,
    has_candidate: bool,
) -> None:
    problem = Problem(stocks=(), demands=())
    diagnostics = _diagnostics(backend_status)
    candidate = (
        UnverifiedCandidate((), ObjectiveValue(0, 0, 0))
        if has_candidate
        else None
    )
    backend = RecordingBackend(
        UnverifiedBackendResult(candidate, diagnostics)
    )
    _install_backend(monkeypatch, "exact_enumerated", backend)

    result = solve(problem)

    assert result.solution_status is solution_status
    assert result.diagnostics == replace(
        diagnostics,
        backend_id=backend.backend_id,
        backend_version=backend.availability.backend_version,
    )
    assert (result.objective_value is not None) is has_candidate


@pytest.mark.parametrize(
    "backend_status",
    [
        BackendStatus.OPTIMAL,
        BackendStatus.FEASIBLE,
        BackendStatus.LIMIT_REACHED,
    ],
)
def test_feasible_backend_outputs_have_verification_reports(
    monkeypatch: pytest.MonkeyPatch,
    backend_status: BackendStatus,
) -> None:
    problem = Problem(stocks=(), demands=())
    backend = RecordingBackend(_empty_candidate_result(backend_status))
    _install_backend(monkeypatch, "exact_enumerated", backend)

    result = solve(problem)

    assert result.verification_report is not None
    assert result.verification_report.is_valid


@pytest.mark.parametrize(
    "backend_status",
    [
        BackendStatus.OPTIMAL,
        BackendStatus.FEASIBLE,
        BackendStatus.LIMIT_REACHED,
    ],
)
def test_invalid_feasible_backend_outputs_raise_internal_error(
    monkeypatch: pytest.MonkeyPatch,
    backend_status: BackendStatus,
) -> None:
    problem = Problem(
        stocks=(Stock("stock", "steel", 10, 1),),
        demands=(Demand("demand", "steel", 5, 1),),
    )
    backend = RecordingBackend(_empty_candidate_result(backend_status))
    _install_backend(monkeypatch, "exact_enumerated", backend)

    with pytest.raises(
        InternalVerificationError,
        match="demand_underproduced",
    ):
        solve(problem)


@pytest.mark.parametrize(
    "backend_status",
    [BackendStatus.OPTIMAL, BackendStatus.FEASIBLE],
)
def test_feasible_status_without_candidate_raises_internal_error(
    monkeypatch: pytest.MonkeyPatch,
    backend_status: BackendStatus,
) -> None:
    problem = Problem(stocks=(), demands=())
    backend = RecordingBackend(
        UnverifiedBackendResult(None, _diagnostics(backend_status))
    )
    _install_backend(monkeypatch, "exact_enumerated", backend)

    with pytest.raises(
        InternalVerificationError,
        match="without a candidate",
    ):
        solve(problem)


def test_limit_without_candidate_has_no_verification_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    problem = Problem(stocks=(), demands=())
    backend = RecordingBackend(
        UnverifiedBackendResult(
            None,
            _diagnostics(BackendStatus.LIMIT_REACHED),
        )
    )
    _install_backend(monkeypatch, "exact_enumerated", backend)

    result = solve(problem)

    assert result.solution_status is SolutionStatus.LIMIT
    assert result.verification_report is None


def test_unavailable_backend_raises_the_documented_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    problem = Problem(stocks=(), demands=())
    backend = RecordingBackend(
        _empty_candidate_result(),
        availability=BackendAvailability(
            available=False,
            reason="solver executable missing",
        ),
    )
    _install_backend(monkeypatch, "exact_enumerated", backend)

    with pytest.raises(
        BackendUnavailableError,
        match="solver executable missing",
    ):
        solve(problem)

    assert backend.calls == ["check_availability"]


def test_unsupported_problem_returns_a_structured_solution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    problem = Problem(stocks=(), demands=())
    backend = RecordingBackend(
        _empty_candidate_result(),
        support=BackendSupport(supported=False, reason="too large"),
    )
    _install_backend(monkeypatch, "exact_enumerated", backend)

    result = solve(problem)

    assert result.solution_status is SolutionStatus.UNSUPPORTED
    assert result.diagnostics.termination_reason is (
        TerminationReason.UNSUPPORTED_PROBLEM
    )
    assert backend.calls == ["check_availability", "check_support"]


def test_unknown_backend_id_is_rejected_after_validation() -> None:
    problem = Problem(
        stocks=(Stock("stock", "steel", 10, 1),),
        demands=(Demand("demand", "steel", 5, 1),),
    )

    with pytest.raises(ValueError, match="Unknown backend 'missing'"):
        solve(problem, SolveOptions(backend="missing"))


INVALID_BACKEND_VALUES = [
    None,
    123,
    1.5,
    True,
    False,
    [],
    "",
]


INVALID_TIME_LIMIT_SECONDS_VALUES = [
    float("nan"),
    float("inf"),
    float("-inf"),
    True,
    False,
    0,
    0.0,
    -1,
    -1.5,
    "5",
    [5],
]


INVALID_PATTERN_LIMIT_VALUES = [
    float("nan"),
    float("inf"),
    True,
    False,
    0,
    -1,
    1.5,
    5.0,
    "5",
    [5],
]


def _assert_backend_selection_never_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> Mock:
    solve_module = import_module("saw.solve")
    guard = Mock(side_effect=AssertionError("backend selection must not run"))
    monkeypatch.setattr(solve_module, "_select_backend", guard)
    return guard


@pytest.mark.parametrize("backend", INVALID_BACKEND_VALUES)
def test_invalid_backend_identifier_is_rejected_before_backend_selection(
    monkeypatch: pytest.MonkeyPatch,
    backend: object,
) -> None:
    problem = Problem(stocks=(), demands=())
    guard = _assert_backend_selection_never_runs(monkeypatch)

    with pytest.raises(ValueError, match="backend"):
        solve(problem, SolveOptions(backend=backend))  # type: ignore[arg-type]

    guard.assert_not_called()


@pytest.mark.parametrize("time_limit_seconds", INVALID_TIME_LIMIT_SECONDS_VALUES)
def test_invalid_time_limit_seconds_is_rejected_before_backend_selection(
    monkeypatch: pytest.MonkeyPatch,
    time_limit_seconds: object,
) -> None:
    problem = Problem(stocks=(), demands=())
    guard = _assert_backend_selection_never_runs(monkeypatch)

    with pytest.raises(ValueError, match="time_limit_seconds"):
        solve(
            problem,
            SolveOptions(time_limit_seconds=time_limit_seconds),  # type: ignore[arg-type]
        )

    guard.assert_not_called()


@pytest.mark.parametrize("pattern_limit", INVALID_PATTERN_LIMIT_VALUES)
def test_invalid_pattern_limit_is_rejected_before_backend_selection(
    monkeypatch: pytest.MonkeyPatch,
    pattern_limit: object,
) -> None:
    problem = Problem(stocks=(), demands=())
    guard = _assert_backend_selection_never_runs(monkeypatch)

    with pytest.raises(ValueError, match="pattern_limit"):
        solve(
            problem,
            SolveOptions(pattern_limit=pattern_limit),  # type: ignore[arg-type]
        )

    guard.assert_not_called()


def test_invalid_options_never_invoke_a_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    problem = Problem(stocks=(), demands=())
    backend = RecordingBackend(_empty_candidate_result())
    _install_backend(monkeypatch, "exact_enumerated", backend)

    with pytest.raises(ValueError, match="pattern_limit"):
        solve(problem, SolveOptions(pattern_limit=-1))

    assert backend.calls == []


@pytest.mark.parametrize(
    ("time_limit_seconds", "pattern_limit"),
    [
        (None, None),
        (30.0, 50_000),
        (0.001, 1),
    ],
)
def test_valid_options_pass_validation_and_reach_the_backend(
    monkeypatch: pytest.MonkeyPatch,
    time_limit_seconds: float | None,
    pattern_limit: int | None,
) -> None:
    problem = Problem(stocks=(), demands=())
    backend = RecordingBackend(_empty_candidate_result())
    _install_backend(monkeypatch, "exact_enumerated", backend)

    solve(
        problem,
        SolveOptions(
            time_limit_seconds=time_limit_seconds,
            pattern_limit=pattern_limit,
        ),
    )

    assert backend.calls == ["check_availability", "check_support", "solve"]


def test_option_validation_runs_after_problem_validation() -> None:
    invalid_problem = Problem(
        stocks=(Stock("stock", "steel", 0, 1),),
        demands=(),
    )

    with pytest.raises(ValidationError):
        solve(invalid_problem, SolveOptions(pattern_limit=-1))


def test_option_validation_runs_before_unknown_backend_is_reported() -> None:
    problem = Problem(stocks=(), demands=())

    with pytest.raises(ValueError, match="pattern_limit"):
        solve(problem, SolveOptions(backend="missing", pattern_limit=-1))


def test_unknown_backend_handling_stays_deterministic_across_calls() -> None:
    problem = Problem(
        stocks=(Stock("stock", "steel", 10, 1),),
        demands=(Demand("demand", "steel", 5, 1),),
    )

    for _ in range(3):
        with pytest.raises(ValueError, match="Unknown backend 'missing'"):
            solve(problem, SolveOptions(backend="missing"))
