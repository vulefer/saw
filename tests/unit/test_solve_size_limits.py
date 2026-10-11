from __future__ import annotations

from dataclasses import dataclass, field
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
)
from saw.models import (
    BackendStatus,
    Diagnostics,
    ObjectiveStage,
    SolutionStatus,
    TerminationReason,
)


@dataclass
class RecordingBackend:
    """A minimal stand-in backend that records whether it was ever reached.

    A preflight size-limit outcome must never touch the backend at all, so
    every test that expects a LIMIT result also asserts ``backend.calls ==
    []``.
    """

    selected_id: str = "recording"
    availability: BackendAvailability = field(
        default_factory=lambda: BackendAvailability(available=True)
    )
    support: BackendSupport = field(default_factory=lambda: BackendSupport(supported=True))
    calls: list[str] = field(default_factory=list)

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
        return self.support

    def solve(
        self,
        problem: Problem,
        options: BackendOptions,
    ) -> UnverifiedBackendResult:
        self.calls.append("solve")
        # INFEASIBLE needs no candidate and skips independent verification
        # (see saw.solve.solve), so this stand-in works for arbitrary
        # problem contents without needing a real, verifiable cut plan.
        return UnverifiedBackendResult(
            candidate=None,
            diagnostics=Diagnostics(
                termination_reason=TerminationReason.COMPLETED,
                backend_status=BackendStatus.INFEASIBLE,
                elapsed_time=0.0,
                patterns_generated=0,
                iterations=0,
                completed_objective_stages=(),
                lower_bound=None,
                upper_bound=None,
            ),
        )


def _install_backend(
    monkeypatch: pytest.MonkeyPatch,
    backend: RecordingBackend,
) -> None:
    solve_module = import_module("saw.solve")
    monkeypatch.setattr(solve_module, "_BACKENDS", {"exact_enumerated": backend})


def _stocks(count: int) -> tuple[Stock, ...]:
    return tuple(Stock(f"stock-{index}", "steel", length=10, quantity=1) for index in range(count))


def _demands(count: int) -> tuple[Demand, ...]:
    return tuple(Demand(f"demand-{index}", "steel", length=5, quantity=1) for index in range(count))


def _assert_size_limit_result(result) -> None:
    assert result.solution_status is SolutionStatus.LIMIT
    assert result.diagnostics.termination_reason is TerminationReason.SIZE_LIMIT
    # backend_status is LIMIT_REACHED -- not NOT_RUN -- even for this
    # preflight-only outcome: the version 1 JSON schema's limitDiagnostics
    # requires backend_status: "limit_reached" for every `limit`
    # solution_status (see src/saw/schemas/solution-v1.schema.json and
    # docs/formats.md, "Diagnostics"). NOT_RUN is reserved for
    # `unsupported`.
    assert result.diagnostics.backend_status is BackendStatus.LIMIT_REACHED
    assert result.diagnostics.patterns_generated == 0
    assert result.diagnostics.iterations == 0
    assert result.diagnostics.completed_objective_stages == ()
    assert result.diagnostics.lower_bound is None
    assert result.diagnostics.upper_bound is None
    # Never reached a backend, so neither Python-only field is populated.
    assert result.diagnostics.backend_id is None
    assert result.diagnostics.backend_version is None
    assert result.cut_patterns == ()
    assert result.objective_value is None
    assert result.verification_report is None
    assert result.diagnostics.elapsed_time >= 0.0


# --- problem_size_limit -------------------------------------------------


def test_problem_size_limit_default_is_two_hundred_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    problem = Problem(stocks=_stocks(100), demands=_demands(100))
    backend = RecordingBackend()
    _install_backend(monkeypatch, backend)

    result = solve(problem)

    assert result.solution_status is SolutionStatus.INFEASIBLE
    assert backend.calls == ["check_availability", "check_support", "solve"]


def test_problem_size_limit_boundary_value_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    problem = Problem(stocks=_stocks(150), demands=_demands(50))
    backend = RecordingBackend()
    _install_backend(monkeypatch, backend)

    result = solve(problem, SolveOptions(problem_size_limit=200))

    assert result.solution_status is SolutionStatus.INFEASIBLE
    assert backend.calls == ["check_availability", "check_support", "solve"]


def test_problem_size_limit_first_value_over_boundary_is_rejected_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    problem = Problem(stocks=_stocks(150), demands=_demands(51))
    backend = RecordingBackend()
    _install_backend(monkeypatch, backend)

    result = solve(problem, SolveOptions(problem_size_limit=200))

    _assert_size_limit_result(result)
    assert backend.calls == []


def test_problem_size_limit_can_be_disabled_with_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    problem = Problem(stocks=_stocks(201), demands=())
    backend = RecordingBackend()
    _install_backend(monkeypatch, backend)

    result = solve(
        problem,
        SolveOptions(problem_size_limit=None, numeric_magnitude_limit=None),
    )

    assert result.solution_status is SolutionStatus.INFEASIBLE
    assert backend.calls == ["check_availability", "check_support", "solve"]


# --- numeric_magnitude_limit ---------------------------------------------


@pytest.mark.parametrize(
    "make_problem",
    [
        lambda value: Problem(
            stocks=(Stock("stock", "steel", length=value, quantity=1),),
            demands=(Demand("demand", "steel", length=5, quantity=1),),
        ),
        lambda value: Problem(
            stocks=(Stock("stock", "steel", length=10, quantity=value),),
            demands=(Demand("demand", "steel", length=5, quantity=1),),
        ),
        lambda value: Problem(
            stocks=(Stock("stock", "steel", length=10, quantity=1),),
            demands=(Demand("demand", "steel", length=value, quantity=1),),
        ),
        lambda value: Problem(
            stocks=(Stock("stock", "steel", length=10, quantity=1),),
            demands=(Demand("demand", "steel", length=5, quantity=value),),
        ),
    ],
    ids=["stock_length", "stock_quantity", "demand_length", "demand_quantity"],
)
def test_numeric_magnitude_limit_boundary_is_accepted_per_field(
    monkeypatch: pytest.MonkeyPatch,
    make_problem,
) -> None:
    backend = RecordingBackend()
    _install_backend(monkeypatch, backend)

    result = solve(
        make_problem(1_000),
        SolveOptions(numeric_magnitude_limit=1_000),
    )

    assert result.solution_status is SolutionStatus.INFEASIBLE
    assert backend.calls == ["check_availability", "check_support", "solve"]


@pytest.mark.parametrize(
    "make_problem",
    [
        lambda value: Problem(
            stocks=(Stock("stock", "steel", length=value, quantity=1),),
            demands=(Demand("demand", "steel", length=5, quantity=1),),
        ),
        lambda value: Problem(
            stocks=(Stock("stock", "steel", length=10, quantity=value),),
            demands=(Demand("demand", "steel", length=5, quantity=1),),
        ),
        lambda value: Problem(
            stocks=(Stock("stock", "steel", length=10, quantity=1),),
            demands=(Demand("demand", "steel", length=value, quantity=1),),
        ),
        lambda value: Problem(
            stocks=(Stock("stock", "steel", length=10, quantity=1),),
            demands=(Demand("demand", "steel", length=5, quantity=value),),
        ),
    ],
    ids=["stock_length", "stock_quantity", "demand_length", "demand_quantity"],
)
def test_numeric_magnitude_first_value_over_boundary_is_rejected_safely(
    monkeypatch: pytest.MonkeyPatch,
    make_problem,
) -> None:
    backend = RecordingBackend()
    _install_backend(monkeypatch, backend)

    result = solve(
        make_problem(1_001),
        SolveOptions(numeric_magnitude_limit=1_000),
    )

    _assert_size_limit_result(result)
    assert backend.calls == []


def test_numeric_magnitude_limit_default_is_one_million(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = RecordingBackend()
    _install_backend(monkeypatch, backend)
    problem = Problem(
        stocks=(Stock("stock", "steel", length=1_000_001, quantity=1),),
        demands=(Demand("demand", "steel", length=5, quantity=1),),
    )

    result = solve(problem)

    _assert_size_limit_result(result)
    assert backend.calls == []


def test_numeric_magnitude_limit_can_be_disabled_with_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = RecordingBackend()
    _install_backend(monkeypatch, backend)
    problem = Problem(
        stocks=(Stock("stock", "steel", length=5_000_000, quantity=1),),
        demands=(Demand("demand", "steel", length=5, quantity=1),),
    )

    result = solve(problem, SolveOptions(numeric_magnitude_limit=None))

    assert result.solution_status is SolutionStatus.INFEASIBLE
    assert backend.calls == ["check_availability", "check_support", "solve"]


# --- ordering and never-infeasible guarantees ----------------------------


def test_problem_size_limit_is_checked_before_numeric_magnitude_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    solve_module = import_module("saw.solve")
    guard = Mock(
        side_effect=AssertionError(
            "numeric_magnitude_limit must not be evaluated once "
            "problem_size_limit has already failed"
        )
    )
    monkeypatch.setattr(solve_module, "_exceeds_numeric_magnitude_limit", guard)

    backend = RecordingBackend()
    _install_backend(monkeypatch, backend)
    problem = Problem(
        stocks=(Stock("stock", "steel", length=10_000_000, quantity=1),),
        demands=_demands(300),
    )

    result = solve(
        problem,
        SolveOptions(problem_size_limit=200, numeric_magnitude_limit=1_000),
    )

    _assert_size_limit_result(result)
    guard.assert_not_called()


def test_size_limit_never_claims_infeasibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = RecordingBackend()
    _install_backend(monkeypatch, backend)
    problem = Problem(stocks=_stocks(201), demands=())

    result = solve(problem)

    assert result.solution_status is not SolutionStatus.INFEASIBLE
    assert result.solution_status is SolutionStatus.LIMIT


def test_size_limit_short_circuits_before_backend_availability_is_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = RecordingBackend(
        availability=BackendAvailability(available=False, reason="should never be consulted")
    )
    _install_backend(monkeypatch, backend)
    problem = Problem(stocks=_stocks(201), demands=())

    result = solve(problem)

    _assert_size_limit_result(result)
    assert backend.calls == []


# --- option validation ----------------------------------------------------


INVALID_LIMIT_VALUES = [
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
):
    solve_module = import_module("saw.solve")
    guard = Mock(side_effect=AssertionError("backend selection must not run"))
    monkeypatch.setattr(solve_module, "_select_backend", guard)
    return guard


@pytest.mark.parametrize("problem_size_limit", INVALID_LIMIT_VALUES)
def test_invalid_problem_size_limit_is_rejected_before_backend_selection(
    monkeypatch: pytest.MonkeyPatch,
    problem_size_limit: object,
) -> None:
    problem = Problem(stocks=(), demands=())
    guard = _assert_backend_selection_never_runs(monkeypatch)

    with pytest.raises(ValueError, match="problem_size_limit"):
        solve(
            problem,
            SolveOptions(problem_size_limit=problem_size_limit),  # type: ignore[arg-type]
        )

    guard.assert_not_called()


@pytest.mark.parametrize("numeric_magnitude_limit", INVALID_LIMIT_VALUES)
def test_invalid_numeric_magnitude_limit_is_rejected_before_backend_selection(
    monkeypatch: pytest.MonkeyPatch,
    numeric_magnitude_limit: object,
) -> None:
    problem = Problem(stocks=(), demands=())
    guard = _assert_backend_selection_never_runs(monkeypatch)

    with pytest.raises(ValueError, match="numeric_magnitude_limit"):
        solve(
            problem,
            SolveOptions(
                numeric_magnitude_limit=numeric_magnitude_limit,  # type: ignore[arg-type]
            ),
        )

    guard.assert_not_called()
