from __future__ import annotations

from dataclasses import dataclass

import pulp

from saw.backends.base import BackendOptions
from saw.backends.pulp_backend import PulpCbcAdapter
from saw.models import BackendStatus, Problem
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


# configures the bounded stock constraint inside the PuLP model
def add_exact_stock_constraints(
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

# this creates an LpProblem instance that only minimizes the number of stocks used, hence called primary
def build_primary_stock_count_model(
        problem: Problem,
        patterns: tuple[Pattern, ...],
) -> tuple[
    pulp.LpProblem,
    dict[Pattern, pulp.LpVariable],
    pulp.LpAffineExpression, # to be used in the later stages of the optimization process
]:
    model = pulp.LpProblem(
        "primary_stock_count",
        pulp.LpMinimize,
    )

    pattern_variables = build_pattern_variables(problem, patterns)

    add_exact_demand_constraints(model, problem, pattern_variables)
    add_exact_stock_constraints(model, problem, pattern_variables)

    stock_count = pulp.lpSum(pattern_variables.values())
    model.setObjective(stock_count)

    return model, pattern_variables, stock_count


# wraps the result of the primary model
@dataclass(frozen=True, slots=True)
class PrimaryStockCountResult:
    backend_status: BackendStatus
    optimal_stock_count: int | None


def solve_primary_stock_count_stage(
    model: pulp.LpProblem,
    stock_count: pulp.LpAffineExpression,
    adapter: PulpCbcAdapter,
    options: BackendOptions,
) -> PrimaryStockCountResult:
    backend_status = adapter.solve(model, options)

    if backend_status is not BackendStatus.OPTIMAL:
        return PrimaryStockCountResult(
            backend_status=backend_status,
            optimal_stock_count=None,
        )

    raw_stock_count = pulp.value(stock_count)

    if raw_stock_count is None:
        raise RuntimeError(
            "CBC reported an optimal result without an objective value."
        )

    rounded_stock_count = round(raw_stock_count)

    if abs(raw_stock_count - rounded_stock_count) > 1e-7:
        raise RuntimeError(
            "The optimal stock count was not integral."
        )

    return PrimaryStockCountResult(
        backend_status=backend_status,
        optimal_stock_count=rounded_stock_count,
    )

# "between the steps" function that returns a LpAffineExpression that describes the total leftover
def build_total_leftover_expression(
    problem: Problem,
    pattern_variables: dict[Pattern, pulp.LpVariable],
) -> pulp.LpAffineExpression:
    stocks_by_id = {
        stock.stock_id: stock
        for stock in problem.stocks
    }
    demands_by_id = {
        demand.demand_id : demand
        for demand in problem.demands
    }

    # this calculates the leftover for every pattern and its stock and multiplies it by the
    # repetition of that pattern in the solution to calculate the total leftover
    return pulp.lpSum(
        (
        stocks_by_id[pattern.stock_id].length
         - sum(demands_by_id[demand_id].length * piece_count
               for demand_id, piece_count in pattern.piece_counts.items())
        ) * variable
    for pattern, variable in pattern_variables.items()
    )

# this is the intended "whole-step" function that uses the previous function to add the optimal stock
# constraint to the problem
def configure_secondary_leftover_stage(
    model: pulp.LpProblem,
    problem: Problem,
    pattern_variables: dict[Pattern, pulp.LpVariable],
    stock_count: pulp.LpAffineExpression,
    optimal_stock_count: int,
) -> pulp.LpAffineExpression:
    model.addConstraint(
        stock_count == optimal_stock_count,
        name="fixed_optimal_stock_count",
    )

    total_leftover = build_total_leftover_expression(
        problem,
        pattern_variables,
    )
    model.setObjective(total_leftover)

    return total_leftover

# wraps the result of the secondary model
@dataclass(frozen=True, slots=True)
class SecondaryLeftoverResult:
    backend_status: BackendStatus
    optimal_total_leftover: int | None

def solve_secondary_leftover_stage(
    model: pulp.LpProblem,
    total_leftover: pulp.LpAffineExpression,
    adapter: PulpCbcAdapter,
    options: BackendOptions,
) -> SecondaryLeftoverResult:
    backend_status = adapter.solve(model, options)

    if backend_status is not BackendStatus.OPTIMAL:
        return SecondaryLeftoverResult(
            backend_status=backend_status,
            optimal_total_leftover=None,
        )

    raw_total_leftover = pulp.value(total_leftover)

    if raw_total_leftover is None:
        raise RuntimeError(
            "CBC reported an optimal result without an objective value."
        )

    rounded_total_leftover = round(raw_total_leftover)

    if abs(raw_total_leftover - rounded_total_leftover) > 1e-7:
        raise RuntimeError(
            "The optimal total leftover was not integral."
        )

    return SecondaryLeftoverResult(
        backend_status=backend_status,
        optimal_total_leftover=rounded_total_leftover,
    )