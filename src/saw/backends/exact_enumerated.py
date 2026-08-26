from __future__ import annotations

from dataclasses import dataclass

import pulp

from saw import Stock
from saw.backends.base import (
    BackendOptions,
    UnverifiedBackendResult,
    UnverifiedCandidate,
)
from saw.backends.pulp_backend import PulpCbcAdapter
from saw.models import (
    BackendStatus,
    CutPattern,
    Diagnostics,
    ObjectiveStage,
    ObjectiveValue,
    Problem,
    SolutionStatus,
    TerminationReason,
)
from saw.patterns import Pattern


_INTEGRALITY_TOLERANCE = 1e-7


def translate_exact_backend_status(
    backend_status: BackendStatus,
) -> SolutionStatus:
    return {
        BackendStatus.OPTIMAL: SolutionStatus.OPTIMAL,
        BackendStatus.FEASIBLE: SolutionStatus.FEASIBLE,
        BackendStatus.INFEASIBLE: SolutionStatus.INFEASIBLE,
        BackendStatus.LIMIT_REACHED: SolutionStatus.LIMIT,
        BackendStatus.NOT_RUN: SolutionStatus.ERROR,
        BackendStatus.UNBOUNDED: SolutionStatus.ERROR,
        BackendStatus.NOT_SOLVED: SolutionStatus.ERROR,
        BackendStatus.FAILED: SolutionStatus.ERROR,
        BackendStatus.UNKNOWN: SolutionStatus.ERROR,
    }.get(backend_status, SolutionStatus.ERROR)



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

"""
 --- Stage 1: minimize the total number of stock pieces used. ---
"""
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

"""
--- Stage 2: minimize total leftover while preserving the optimal stock count. ---
"""

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

"""
--- Stage 3: minimize distinct positive-use patterns while preserving earlier optima. ---
"""
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

    for index, (pattern, pattern_variable) in enumerate(pattern_variables.items()):
        indicator = pulp.LpVariable(
            name = f"pattern_used_{index:06d}",
            cat = pulp.LpBinary,
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

    distinct_pattern_count = pulp.lpSum(
        pattern_use_indicators.values()
    )
    model.setObjective(distinct_pattern_count)

    return distinct_pattern_count

@dataclass(frozen=True, slots=True)
class TertiaryDistinctPatternCountResult:
    backend_status: BackendStatus
    optimal_distinct_pattern_count: int | None

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

    raw_distinct_pattern_count = pulp.value(distinct_pattern_count)

    if raw_distinct_pattern_count is None:
        raise RuntimeError(
            "CBC reported an optimal result without an objective value."
        )

    rounded_distinct_pattern_count = round(raw_distinct_pattern_count)

    if abs(raw_distinct_pattern_count - rounded_distinct_pattern_count) > 1e-7:
        raise RuntimeError(
            "The optimal distinct pattern count was not integral."
        )

    return TertiaryDistinctPatternCountResult(
        backend_status=backend_status,
        optimal_distinct_pattern_count=rounded_distinct_pattern_count,
    )

# converts the results to a list of CutPatterns
def extract_positive_cut_patterns(
    pattern_variables: dict[Pattern, pulp.LpVariable],
) -> tuple[CutPattern, ...]:

    cut_patterns: list[CutPattern] = []

    for pattern, variable in sorted(
        pattern_variables.items(),
        key=lambda item: _pattern_sort_key(item[0]),
    ):
        raw_repetitions = pulp.value(variable)

        if raw_repetitions is None:
            raise RuntimeError(
                f"Solver returned no value for pattern variable {variable.name!r}."
            )

        repetitions = round(raw_repetitions)

        if abs(raw_repetitions - repetitions) > _INTEGRALITY_TOLERANCE:
            raise RuntimeError(
                f"Pattern variable {variable.name!r} was not integral."
            )

        if repetitions < 0:
            raise RuntimeError(
                f"Pattern variable {variable.name!r} was negative."
            )

        if repetitions > 0:
            cut_patterns.append(
                CutPattern(
                    pattern=pattern,
                    repetitions=repetitions,
                )
            )

    return tuple(cut_patterns)

# builds the whole UnverifiedBackendResult from the result of the solver
def extract_backend_output(
    pattern_variables: dict[Pattern, pulp.LpVariable],
    primary_result: PrimaryStockCountResult,
    secondary_result: SecondaryLeftoverResult,
    tertiary_result: TertiaryDistinctPatternCountResult,
    *,
    elapsed_time: float,
) -> UnverifiedBackendResult:

    stage_results = (
        (
            ObjectiveStage.STOCK_PIECE_COUNT,
            primary_result.backend_status,
            primary_result.optimal_stock_count,
        ),
        (
            ObjectiveStage.TOTAL_LEFTOVER,
            secondary_result.backend_status,
            secondary_result.optimal_total_leftover,
        ),
        (
            ObjectiveStage.DISTINCT_PATTERN_COUNT,
            tertiary_result.backend_status,
            tertiary_result.optimal_distinct_pattern_count,
        ),
    )

    for stage, backend_status, objective_value in stage_results:
        if backend_status is not BackendStatus.OPTIMAL or objective_value is None:
            raise RuntimeError(
                f"Cannot extract backend output before {stage.value!r} "
                "has a proven optimum."
            )

    stock_piece_count = primary_result.optimal_stock_count
    total_leftover = secondary_result.optimal_total_leftover
    distinct_pattern_count = tertiary_result.optimal_distinct_pattern_count

    # The checks above narrow these values logically, while the explicit
    # assertions make that invariant visible to static type checkers.
    assert stock_piece_count is not None
    assert total_leftover is not None
    assert distinct_pattern_count is not None

    candidate = UnverifiedCandidate(
        cut_patterns=extract_positive_cut_patterns(pattern_variables),
        objective_value=ObjectiveValue(
            stock_piece_count=stock_piece_count,
            total_leftover=total_leftover,
            distinct_pattern_count=distinct_pattern_count,
        ),
    )
    diagnostics = Diagnostics(
        termination_reason=TerminationReason.COMPLETED,
        backend_status=tertiary_result.backend_status,
        elapsed_time=elapsed_time,
        patterns_generated=len(pattern_variables),
        iterations=1,
        completed_objective_stages=tuple(
            stage
            for stage, _, _ in stage_results
        ),
        lower_bound=stock_piece_count,
        upper_bound=stock_piece_count,
    )

    return UnverifiedBackendResult(
        candidate=candidate,
        diagnostics=diagnostics,
    )
