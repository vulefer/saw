from __future__ import annotations

from dataclasses import dataclass

import pulp

from saw.backends.base import BackendOptions
from saw.backends.exact_enumerated.model import (
    add_exact_demand_constraints,
    add_bounded_stock_constraints,
    build_pattern_use_indicators,
    build_pattern_variables,
    build_total_leftover_expression,
)
from saw.backends.pulp_backend import PulpCbcAdapter
from saw.models import BackendStatus, Problem
from saw.patterns import Pattern


_INTEGRALITY_TOLERANCE = 1e-7

# wrappers for different optimization stage results
@dataclass(frozen=True, slots=True)
class PrimaryStockCountResult:
    backend_status: BackendStatus
    optimal_stock_count: int | None

@dataclass(frozen=True, slots=True)
class SecondaryLeftoverResult:
    backend_status: BackendStatus
    optimal_total_leftover: int | None

@dataclass(frozen=True, slots=True)
class TertiaryDistinctPatternCountResult:
    backend_status: BackendStatus
    optimal_distinct_pattern_count: int | None

# constructs the primary (stock count) expression for the model
def build_primary_stock_count_model(
    problem: Problem,
    patterns: tuple[Pattern, ...],
) -> tuple[
    pulp.LpProblem,
    dict[Pattern, pulp.LpVariable],
    pulp.LpAffineExpression,
]:
    model = pulp.LpProblem(
        "primary_stock_count",
        pulp.LpMinimize,
    )
    pattern_variables = build_pattern_variables(problem, patterns)

    add_exact_demand_constraints(model, problem, pattern_variables)
    add_bounded_stock_constraints(model, problem, pattern_variables)

    stock_count = pulp.lpSum(pattern_variables.values())
    model.setObjective(stock_count)

    return model, pattern_variables, stock_count

# helper function that contains the shared part of the code that checks if the result is integral
def _read_integral_objective(
    expression: pulp.LpAffineExpression,
    *,
    description: str,
) -> int:
    raw_value = pulp.value(expression)

    if raw_value is None:
        raise RuntimeError(
            "CBC reported an optimal result without an objective value."
        )

    rounded_value = round(raw_value)

    if abs(raw_value - rounded_value) > _INTEGRALITY_TOLERANCE:
        raise RuntimeError(f"The optimal {description} was not integral.")

    return rounded_value

# function that solves the primary optimization
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

    return PrimaryStockCountResult(
        backend_status=backend_status,
        optimal_stock_count=_read_integral_objective(
            stock_count,
            description="stock count",
        ),
    )

# configures the optimal stock count constraint and sets the objective to the total leftover
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

# function that solves the secondary optimization
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

    return SecondaryLeftoverResult(
        backend_status=backend_status,
        optimal_total_leftover=_read_integral_objective(
            total_leftover,
            description="total leftover",
        ),
    )

# configures the total leftover constraint and sets the objective to the distinct pattern count
def configure_tertiary_pattern_count_stage(
    model: pulp.LpProblem,
    problem: Problem,
    pattern_variables: dict[Pattern, pulp.LpVariable],
    total_leftover: pulp.LpAffineExpression,
    optimal_total_leftover: int,
) -> pulp.LpAffineExpression:
    model.addConstraint(
        total_leftover == optimal_total_leftover,
        name="fixed_optimal_total_leftover",
    )

    pattern_use_indicators = build_pattern_use_indicators(
        model=model,
        problem=problem,
        pattern_variables=pattern_variables,
    )
    distinct_pattern_count = pulp.lpSum(pattern_use_indicators.values())
    model.setObjective(distinct_pattern_count)

    return distinct_pattern_count

# function that solves the ternary optimization
def solve_tertiary_pattern_count_stage(
    model: pulp.LpProblem,
    distinct_pattern_count: pulp.LpAffineExpression,
    adapter: PulpCbcAdapter,
    options: BackendOptions,
) -> TertiaryDistinctPatternCountResult:
    backend_status = adapter.solve(model, options)

    if backend_status is not BackendStatus.OPTIMAL:
        return TertiaryDistinctPatternCountResult(
            backend_status=backend_status,
            optimal_distinct_pattern_count=None,
        )

    return TertiaryDistinctPatternCountResult(
        backend_status=backend_status,
        optimal_distinct_pattern_count=_read_integral_objective(
            distinct_pattern_count,
            description="distinct pattern count",
        ),
    )
