from __future__ import annotations

import pulp

from saw.backends.base import (
    UnverifiedBackendResult,
    UnverifiedCandidate,
)
from saw.backends.exact_enumerated.model import pattern_sort_key
from saw.backends.exact_enumerated.stages import (
    PrimaryStockCountResult,
    SecondaryLeftoverResult,
    TertiaryDistinctPatternCountResult,
)
from saw.models import (
    BackendStatus,
    CutPattern,
    Diagnostics,
    ObjectiveStage,
    ObjectiveValue,
    SolutionStatus,
    TerminationReason,
)
from saw.patterns import Pattern


_INTEGRALITY_TOLERANCE = 1e-7

# mapping of backend -> solution statuses
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

# constructs a list of CutPattern instances from the result of the optimization
def extract_positive_cut_patterns(
    pattern_variables: dict[Pattern, pulp.LpVariable],
) -> tuple[CutPattern, ...]:
    cut_patterns: list[CutPattern] = []

    for pattern, variable in sorted(
        pattern_variables.items(),
        key=lambda item: pattern_sort_key(item[0]),
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

# constructs the whole UnverifiedBackendResult instance from the result of the optimization
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
