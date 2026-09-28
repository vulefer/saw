from __future__ import annotations

from saw import Demand, Problem, SolveOptions, Stock, solve
from saw.models import BackendStatus, SolutionStatus, TerminationReason


def _multi_pattern_problem() -> Problem:
    # Enumerates to exactly 3 non-empty patterns: {piece-4: 1},
    # {piece-6: 1}, and {piece-4: 1, piece-6: 1}. The combined pattern is
    # the unique proven optimum (1 stock piece, 0 leftover).
    return Problem(
        stocks=(Stock("stock-10", "steel", 10, 1),),
        demands=(
            Demand("piece-6", "steel", 6, 1),
            Demand("piece-4", "steel", 4, 1),
        ),
    )


def test_pattern_limit_no_longer_reports_the_problem_as_unsupported() -> None:
    result = solve(_multi_pattern_problem(), SolveOptions(pattern_limit=2))

    assert result.solution_status is not SolutionStatus.UNSUPPORTED


def test_pattern_limit_hit_returns_limit_end_to_end() -> None:
    # The problem enumerates to 3 patterns; a limit of 2 is hit attempting
    # the 3rd.
    result = solve(_multi_pattern_problem(), SolveOptions(pattern_limit=2))

    assert result.solution_status is SolutionStatus.LIMIT
    assert result.diagnostics.termination_reason is TerminationReason.PATTERN_LIMIT
    assert result.diagnostics.backend_status is BackendStatus.LIMIT_REACHED
    assert result.diagnostics.patterns_generated == 2
    assert result.diagnostics.completed_objective_stages == ()
    assert result.diagnostics.lower_bound is None
    assert result.diagnostics.upper_bound is None
    assert result.cut_patterns == ()
    assert result.objective_value is None
    assert result.verification_report is None
    assert result.diagnostics.elapsed_time >= 0.0


def test_pattern_limit_never_claims_infeasibility_or_optimality() -> None:
    result = solve(_multi_pattern_problem(), SolveOptions(pattern_limit=2))

    assert result.solution_status is not SolutionStatus.INFEASIBLE
    assert result.solution_status is not SolutionStatus.OPTIMAL


def test_pattern_limit_exactly_matching_the_total_still_solves_to_optimality() -> None:
    result = solve(_multi_pattern_problem(), SolveOptions(pattern_limit=3))

    assert result.solution_status is SolutionStatus.OPTIMAL
    assert result.diagnostics.termination_reason is TerminationReason.COMPLETED
    assert result.diagnostics.backend_status is BackendStatus.OPTIMAL
    assert result.diagnostics.patterns_generated == 3
    assert result.objective_value is not None
    assert result.objective_value.stock_piece_count == 1
    assert result.verification_report is not None
    assert result.verification_report.is_valid


def test_pattern_limit_disabled_with_none_matches_unlimited_behavior() -> None:
    limited = solve(_multi_pattern_problem(), SolveOptions(pattern_limit=None))
    unlimited = solve(_multi_pattern_problem())

    assert limited.solution_status is SolutionStatus.OPTIMAL
    assert limited.diagnostics.patterns_generated == 3
    assert limited.objective_value == unlimited.objective_value


def test_pattern_limit_hit_is_deterministic_across_repeated_solves() -> None:
    problem = _multi_pattern_problem()

    first = solve(problem, SolveOptions(pattern_limit=2))
    second = solve(problem, SolveOptions(pattern_limit=2))

    assert first.solution_status == second.solution_status
    assert first.diagnostics.termination_reason == second.diagnostics.termination_reason
    assert first.diagnostics.patterns_generated == second.diagnostics.patterns_generated
