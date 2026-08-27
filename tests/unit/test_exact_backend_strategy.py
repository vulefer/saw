from __future__ import annotations

import pulp

from saw import Demand, Problem, Stock
from saw.backends.base import (
    BackendAvailability,
    BackendOptions,
    OptimizationBackend,
)
from saw.backends.exact_enumerated import (
    ExactEnumeratedBackend,
    solve_exact_enumerated_pipeline,
)
from saw.backends.pulp_backend import PulpAdapterError
from saw.models import (
    BackendStatus,
    ObjectiveStage,
    TerminationReason,
)
from saw.patterns import enumerate_patterns_for_problem


class FakeAdapter:
    def __init__(
        self,
        status: BackendStatus = BackendStatus.OPTIMAL,
        *,
        error: Exception | None = None,
    ) -> None:
        self.status = status
        self.error = error
        self.solve_calls = 0

    def check_availability(self) -> BackendAvailability:
        return BackendAvailability(available=True, backend_version="test")

    def solve(
        self,
        model: pulp.LpProblem,
        options: BackendOptions,
    ) -> BackendStatus:
        self.solve_calls += 1
        if self.error is not None:
            raise self.error
        return self.status


class FeasibleIncumbentAdapter(FakeAdapter):
    def solve(
        self,
        model: pulp.LpProblem,
        options: BackendOptions,
    ) -> BackendStatus:
        for variable in model.variables():
            variable.varValue = 1
        return BackendStatus.FEASIBLE


def _one_piece_problem() -> Problem:
    return Problem(
        stocks=(Stock("stock-10", "steel", 10, 1),),
        demands=(Demand("piece-6", "steel", 6, 1),),
    )


def test_exact_backend_is_an_optimization_strategy() -> None:
    backend = ExactEnumeratedBackend(FakeAdapter())  # type: ignore[arg-type]

    assert isinstance(backend, OptimizationBackend)
    assert backend.backend_id == "exact_enumerated"
    assert backend.check_availability().available is True
    assert backend.capabilities().objective_stages == frozenset(ObjectiveStage)


def test_exact_backend_reports_unsupported_pattern_limit() -> None:
    backend = ExactEnumeratedBackend(FakeAdapter())  # type: ignore[arg-type]

    support = backend.check_support(
        _one_piece_problem(),
        BackendOptions(pattern_limit=10),
    )

    assert support.supported is False
    assert support.reason is not None
    assert "pattern limit" in support.reason


def test_primary_infeasibility_is_returned_as_a_structured_result() -> None:
    problem = _one_piece_problem()
    patterns = enumerate_patterns_for_problem(problem)

    result = solve_exact_enumerated_pipeline(
        problem,
        patterns,
        FakeAdapter(BackendStatus.INFEASIBLE),  # type: ignore[arg-type]
        BackendOptions(),
        elapsed_time=1.5,
    )

    assert result.candidate is None
    assert result.diagnostics.backend_status is BackendStatus.INFEASIBLE
    assert result.diagnostics.termination_reason is TerminationReason.COMPLETED
    assert result.diagnostics.elapsed_time == 1.5
    assert result.diagnostics.completed_objective_stages == ()


def test_feasible_limit_incumbent_is_returned_without_optimality_claim() -> None:
    problem = _one_piece_problem()
    patterns = enumerate_patterns_for_problem(problem)

    result = solve_exact_enumerated_pipeline(
        problem,
        patterns,
        FeasibleIncumbentAdapter(),  # type: ignore[arg-type]
        BackendOptions(time_limit_seconds=0.1),
        elapsed_time=0.1,
    )

    assert result.candidate is not None
    assert result.candidate.objective_value.stock_piece_count == 1
    assert result.candidate.objective_value.total_leftover == 4
    assert result.candidate.objective_value.distinct_pattern_count == 1
    assert result.diagnostics.backend_status is BackendStatus.FEASIBLE
    assert result.diagnostics.termination_reason is TerminationReason.TIME_LIMIT
    assert result.diagnostics.completed_objective_stages == ()


def test_adapter_failure_becomes_a_structured_backend_error() -> None:
    adapter = FakeAdapter(error=PulpAdapterError("solver failed"))
    backend = ExactEnumeratedBackend(adapter)  # type: ignore[arg-type]

    result = backend.solve(_one_piece_problem(), BackendOptions())

    assert result.candidate is None
    assert result.diagnostics.backend_status is BackendStatus.FAILED
    assert result.diagnostics.termination_reason is (
        TerminationReason.BACKEND_FAILURE
    )
    assert adapter.solve_calls == 1
