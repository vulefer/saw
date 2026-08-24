from __future__ import annotations

import pulp

from saw.models import Problem
from saw.patterns import Pattern


"""this function returns a dictionary-like structure:
(
    "stock-10",
    (
        ("demand-4", 1),
        ("demand-6", 1),
    ),
)
"""
def _pattern_sort_key(
    pattern: Pattern,
) -> tuple[str, tuple[tuple[str, int], ...]]:
    return pattern.stock_id, tuple(pattern.piece_counts.items())


def build_pattern_variables(
    problem: Problem,
    patterns: tuple[Pattern, ...],
) -> dict[Pattern, pulp.LpVariable]:
    stocks_by_id = {
        stock.stock_id: stock
        for stock in problem.stocks
    }
    canonical_patterns = sorted(
        set(patterns), # removes canonically equivalent patterns
        key=_pattern_sort_key) # sorts the patterns lexicographically by stock_id and (demand_id, piece_count) pairs

    return {
        pattern: pulp.LpVariable(
            name=f"pattern_usage_{index:06d}", # describes the number of times the pattern was used in the solution
            lowBound=0, # makes sure that number is non-negative
            upBound=stocks_by_id[pattern.stock_id].quantity, # makes sure we dont go higher than the quantity we have
            cat=pulp.LpInteger, # declares that this variable is an Integer type
        )
        for index, pattern in enumerate(canonical_patterns)
    }


# configures the exact demand constraint inside the PuLP model
def add_exact_demand_constraints(
    model: pulp.LpProblem,
    problem: Problem,
    pattern_variables: dict[Pattern, pulp.LpVariable],
) -> None:
    for index, demand in enumerate(problem.demands):
        # sums only the pieces associated with this stable demand ID
        total_produced = pulp.lpSum(
            pattern.piece_counts.get(demand.demand_id, 0) * variable
            for pattern, variable in pattern_variables.items()
        )

        # adds the demanded total as a constraint for every demand item
        model.addConstraint(
            total_produced == demand.quantity,
            name=f"exact_demand_{index:06d}",
        )
