from collections import Counter

from saw import (
    Demand,
    ObjectiveValue,
    Problem,
    Solution,
    SolutionStatus,
    Stock,
    solve,
)


def _assert_verified_optimum(
    solution: Solution,
    objective: ObjectiveValue,
) -> None:
    assert solution.solution_status is SolutionStatus.OPTIMAL
    assert solution.objective_value == objective
    assert solution.verification_report is not None
    assert solution.verification_report.is_valid


def _produced_by_demand_id(solution: Solution) -> dict[str, int]:
    produced: Counter[str] = Counter()
    for cut_pattern in solution.cut_patterns:
        for demand_id, piece_count in cut_pattern.piece_counts.items():
            produced[demand_id] += piece_count * cut_pattern.repetitions
    return dict(produced)


def _stock_used(solution: Solution) -> dict[str, int]:
    return {cut_pattern.stock_id: cut_pattern.repetitions for cut_pattern in solution.cut_patterns}


def test_stock_10_uses_the_mixed_6_plus_4_pattern() -> None:
    problem = Problem(
        stocks=(Stock("stock-10", "steel", 10, 1),),
        demands=(
            Demand("six-piece", "steel", 6, 1),
            Demand("four-piece", "steel", 4, 1),
        ),
    )

    solution = solve(problem)

    _assert_verified_optimum(solution, ObjectiveValue(1, 0, 1))
    assert len(solution.cut_patterns) == 1
    assert solution.cut_patterns[0].stock_id == "stock-10"
    assert dict(solution.cut_patterns[0].piece_counts) == {
        "four-piece": 1,
        "six-piece": 1,
    }
    assert solution.cut_patterns[0].repetitions == 1
    assert _produced_by_demand_id(solution) == {
        "six-piece": 1,
        "four-piece": 1,
    }


def test_bounded_stock_is_exhausted_before_using_the_fallback_length() -> None:
    problem = Problem(
        stocks=(
            Stock("stock-6", "steel", 6, 1),
            Stock("stock-10", "steel", 10, 1),
        ),
        demands=(Demand("six-piece", "steel", 6, 2),),
    )

    solution = solve(problem)

    _assert_verified_optimum(solution, ObjectiveValue(2, 4, 2))
    assert _stock_used(solution) == {"stock-10": 1, "stock-6": 1}
    assert _produced_by_demand_id(solution) == {"six-piece": 2}


def test_lower_waste_stock_length_wins_the_secondary_objective() -> None:
    problem = Problem(
        stocks=(
            Stock("stock-12", "steel", 12, 1),
            Stock("stock-10", "steel", 10, 1),
        ),
        demands=(Demand("six-piece", "steel", 6, 1),),
    )

    solution = solve(problem)

    _assert_verified_optimum(solution, ObjectiveValue(1, 4, 1))
    assert _stock_used(solution) == {"stock-10": 1}
    assert _produced_by_demand_id(solution) == {"six-piece": 1}


def test_multiple_materials_are_cut_from_compatible_stock() -> None:
    problem = Problem(
        stocks=(
            Stock("steel-stock", "steel", 10, 1),
            Stock("wood-stock", "wood", 8, 1),
        ),
        demands=(
            Demand("steel-six", "steel", 6, 1),
            Demand("wood-four", "wood", 4, 1),
        ),
    )

    solution = solve(problem)

    _assert_verified_optimum(solution, ObjectiveValue(2, 8, 2))
    assert {
        (cut_pattern.stock_id, tuple(cut_pattern.piece_counts.items()))
        for cut_pattern in solution.cut_patterns
    } == {
        ("steel-stock", (("steel-six", 1),)),
        ("wood-stock", (("wood-four", 1),)),
    }
    assert _produced_by_demand_id(solution) == {
        "steel-six": 1,
        "wood-four": 1,
    }


def test_infeasible_exact_demand_returns_no_cut_plan() -> None:
    problem = Problem(
        stocks=(Stock("stock-10", "steel", 10, 1),),
        demands=(
            Demand("six-piece", "steel", 6, 1),
            Demand("five-piece", "steel", 5, 1),
        ),
    )

    solution = solve(problem)

    assert solution.solution_status is SolutionStatus.INFEASIBLE
    assert solution.cut_patterns == ()
    assert solution.objective_value is None
    assert solution.verification_report is None
