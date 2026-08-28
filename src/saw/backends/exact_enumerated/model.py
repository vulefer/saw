from __future__ import annotations

import pulp

from saw.models import Problem
from saw.patterns import Pattern

#
def pattern_sort_key(
    pattern: Pattern,
) -> tuple[str, tuple[tuple[str, int], ...]]:
    return pattern.stock_id, tuple(pattern.piece_counts.items())

# builds a dictionary of type Pattern: LpVariable for later use when extracting results
def build_pattern_variables(
    problem: Problem,
    patterns: tuple[Pattern, ...],
) -> dict[Pattern, pulp.LpVariable]:
    stocks_by_id = {
        stock.stock_id: stock
        for stock in problem.stocks
    }
    canonical_patterns = sorted(set(patterns), key=pattern_sort_key)

    return {
        pattern: pulp.LpVariable(
            name=f"pattern_usage_{index:06d}",
            lowBound=0,
            upBound=stocks_by_id[pattern.stock_id].quantity,
            cat=pulp.LpInteger,
        )
        for index, pattern in enumerate(canonical_patterns)
    }

# configures the exact demand constraints in the problem
def add_exact_demand_constraints(
    model: pulp.LpProblem,
    problem: Problem,
    pattern_variables: dict[Pattern, pulp.LpVariable],
) -> None:
    for index, demand in enumerate(problem.demands):
        total_produced = pulp.lpSum(
            pattern.piece_counts.get(demand.demand_id, 0) * variable
            for pattern, variable in pattern_variables.items()
        )

        model.addConstraint(
            total_produced == demand.quantity,
            name=f"exact_demand_{index:06d}",
        )

# configures the bounded stock constraints in the problem
def add_bounded_stock_constraints(
    model: pulp.LpProblem,
    problem: Problem,
    pattern_variables: dict[Pattern, pulp.LpVariable],
) -> None:
    for index, stock in enumerate(problem.stocks):
        total_stock_used = pulp.lpSum(
            variable
            for pattern, variable in pattern_variables.items()
            if pattern.stock_id == stock.stock_id
        )

        model.addConstraint(
            total_stock_used <= stock.quantity,
            name=f"bounded_stock_{index:06d}",
        )

# helper function that constructs the secondary (total leftover) expression
def build_total_leftover_expression(
    problem: Problem,
    pattern_variables: dict[Pattern, pulp.LpVariable],
) -> pulp.LpAffineExpression:
    stocks_by_id = {
        stock.stock_id: stock
        for stock in problem.stocks
    }
    demands_by_id = {
        demand.demand_id: demand
        for demand in problem.demands
    }

    return pulp.lpSum(
        (
            stocks_by_id[pattern.stock_id].length
            - sum(
                demands_by_id[demand_id].length * piece_count
                for demand_id, piece_count in pattern.piece_counts.items()
            )
        ) * variable
        for pattern, variable in pattern_variables.items()
    )

# helper function that constructs the dictionary used for constructing the ternary optimization objective
def build_pattern_use_indicators(
    model: pulp.LpProblem,
    problem: Problem,
    pattern_variables: dict[Pattern, pulp.LpVariable],
) -> dict[Pattern, pulp.LpVariable]:
    stocks_by_id = {
        stock.stock_id: stock
        for stock in problem.stocks
    }
    pattern_use_indicators: dict[Pattern, pulp.LpVariable] = {}

    for index, (pattern, pattern_variable) in enumerate(
        pattern_variables.items()
    ):
        indicator = pulp.LpVariable(
            name=f"pattern_used_{index:06d}",
            cat=pulp.LpBinary,
        )
        maximum_usage = stocks_by_id[pattern.stock_id].quantity

        model.addConstraint(
            pattern_variable <= maximum_usage * indicator,
            name=f"link_pattern_use_upper_{index:06d}",
        )
        model.addConstraint(
            indicator <= pattern_variable,
            name=f"link_pattern_use_lower_{index:06d}",
        )
        pattern_use_indicators[pattern] = indicator

    return pattern_use_indicators
