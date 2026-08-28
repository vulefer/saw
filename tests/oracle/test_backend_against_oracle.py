import pytest

from saw import Problem, SolutionStatus, solve, verify_solution
from tests.oracle import generate_small_instances, solve_small_instance


_FEASIBLE_STATUSES = {
    SolutionStatus.OPTIMAL,
    SolutionStatus.FEASIBLE,
}


def _case_id(problem: Problem) -> str:
    stocks = ",".join(
        f"{stock.length}x{stock.quantity}" for stock in problem.stocks
    ) or "none"
    demands = ",".join(
        f"{demand.length}x{demand.quantity}" for demand in problem.demands
    ) or "none"
    return f"stocks={stocks}-demands={demands}"


def _reproduction_context(problem: Problem) -> str:
    return f"\nComplete reproducible instance:\n{problem!r}"


@pytest.mark.parametrize(
    "problem",
    generate_small_instances(),
    ids=_case_id,
)
def test_backend_matches_independent_oracle(problem: Problem) -> None:
    oracle_result = solve_small_instance(problem)
    solution = solve(problem)
    context = _reproduction_context(problem)

    assert solution.solution_status in {
        *_FEASIBLE_STATUSES,
        SolutionStatus.INFEASIBLE,
    }, (
        f"Backend returned an inconclusive status: "
        f"{solution.solution_status!r}.{context}"
    )

    backend_is_feasible = (
        solution.solution_status in _FEASIBLE_STATUSES
    )
    assert backend_is_feasible == oracle_result.feasible, (
        f"Feasibility mismatch: backend={solution.solution_status.value}, "
        f"oracle_feasible={oracle_result.feasible}.{context}"
    )

    if not oracle_result.feasible:
        assert solution.objective_value is None, context
        return

    verification = verify_solution(problem, solution)
    assert verification.is_valid, (
        f"Independent verification failed: "
        f"{verification.findings!r}.{context}"
    )

    assert solution.objective_value is not None, context
    assert oracle_result.objective_value is not None, context

    backend_objective = (
        solution.objective_value.stock_piece_count,
        solution.objective_value.total_leftover,
        solution.objective_value.distinct_pattern_count,
    )
    oracle_objective = (
        oracle_result.objective_value.stock_piece_count,
        oracle_result.objective_value.total_leftover,
        oracle_result.objective_value.distinct_pattern_count,
    )

    assert backend_objective == oracle_objective, (
        f"Objective mismatch: backend={backend_objective}, "
        f"oracle={oracle_objective}.{context}"
    )