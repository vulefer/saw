from __future__ import annotations

from itertools import product
from typing import TypeVar

import pytest

from saw import Demand, Problem, Solution, Stock, solve


_ORDERINGS = (
    (0, 1, 2),
    (1, 2, 0),
    (2, 1, 0),
)

_T = TypeVar("_T")


def _reorder(
    records: tuple[_T, ...],
    ordering: tuple[int, ...],
) -> tuple[_T, ...]:
    return tuple(records[index] for index in ordering)


def _semantic_patterns(
    solution: Solution,
) -> tuple[tuple[str, tuple[tuple[str, int], ...], int], ...]:
    return tuple(sorted(
        (
            cut_pattern.stock_id,
            tuple(sorted(cut_pattern.piece_counts.items())),
            cut_pattern.repetitions,
        )
        for cut_pattern in solution.cut_patterns
    ))


_REPRESENTATIVE_PROBLEMS = (
    pytest.param(
        Problem(
            stocks=(
                Stock("steel-10", "steel", 10, 1),
                Stock("steel-6", "steel", 6, 1),
                Stock("wood-8", "wood", 8, 1),
            ),
            demands=(
                Demand("steel-6", "steel", 6, 2),
                Demand("steel-4", "steel", 4, 1),
                Demand("wood-4", "wood", 4, 2),
            ),
        ),
        id="bounded-multi-material-exact-fill",
    ),
    pytest.param(
        Problem(
            stocks=(
                Stock("steel-12", "steel", 12, 1),
                Stock("steel-10", "steel", 10, 1),
                Stock("aluminium-7", "aluminium", 7, 1),
            ),
            demands=(
                Demand("steel-6", "steel", 6, 1),
                Demand("aluminium-4", "aluminium", 4, 1),
                Demand("aluminium-3", "aluminium", 3, 1),
            ),
        ),
        id="secondary-objective-stock-selection",
    ),
    pytest.param(
        Problem(
            stocks=(
                Stock("steel-10", "steel", 10, 1),
                Stock("steel-4", "steel", 4, 1),
                Stock("wood-8", "wood", 8, 1),
            ),
            demands=(
                Demand("steel-6", "steel", 6, 1),
                Demand("steel-5", "steel", 5, 1),
                Demand("wood-4", "wood", 4, 2),
            ),
        ),
        id="infeasible-bounded-inventory",
    ),
)


@pytest.mark.parametrize("problem", _REPRESENTATIVE_PROBLEMS)
def test_stock_and_demand_permutations_preserve_solution_semantics(
    problem: Problem,
) -> None:
    baseline = solve(problem)
    baseline_patterns = _semantic_patterns(baseline)

    for stock_order, demand_order in product(_ORDERINGS, repeat=2):
        reordered_problem = Problem(
            stocks=_reorder(problem.stocks, stock_order),
            demands=_reorder(problem.demands, demand_order),
        )

        reordered = solve(reordered_problem)
        context = (
            f"stock_order={stock_order}, demand_order={demand_order}"
        )

        assert reordered.solution_status is baseline.solution_status, context
        assert reordered.objective_value == baseline.objective_value, context
        assert _semantic_patterns(reordered) == baseline_patterns, context
