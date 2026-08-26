from __future__ import annotations

from saw.backends.base import BackendOptions, UnverifiedBackendResult
from saw.backends.exact_enumerated.output import extract_backend_output
from saw.backends.exact_enumerated.stages import (
    build_primary_stock_count_model,
    configure_secondary_leftover_stage,
    configure_tertiary_pattern_count_stage,
    solve_primary_stock_count_stage,
    solve_secondary_leftover_stage,
    solve_tertiary_pattern_count_stage,
)
from saw.backends.pulp_backend import PulpCbcAdapter
from saw.models import Problem
from saw.patterns import Pattern


def solve_exact_enumerated_pipeline(
    problem: Problem,
    patterns: tuple[Pattern, ...],
    adapter: PulpCbcAdapter,
    options: BackendOptions,
    *,
    elapsed_time: float,
) -> UnverifiedBackendResult:

    '''---stage 1: stock-count optimization---'''
    model, pattern_variables, stock_count = build_primary_stock_count_model(
        problem,
        patterns,
    )
    primary_result = solve_primary_stock_count_stage(
        model,
        stock_count,
        adapter,
        options,
    )

    if primary_result.optimal_stock_count is None:
        raise RuntimeError(
            "Cannot continue before 'stock_piece_count' has a proven optimum."
        )
    '''---stage 2: total-leftover optimization---'''

    total_leftover = configure_secondary_leftover_stage(
        model,
        problem,
        pattern_variables,
        stock_count,
        primary_result.optimal_stock_count,
    )
    secondary_result = solve_secondary_leftover_stage(
        model,
        total_leftover,
        adapter,
        options,
    )

    if secondary_result.optimal_total_leftover is None:
        raise RuntimeError(
            "Cannot continue before 'total_leftover' has a proven optimum."
        )

    '''---stage 3: distinct-pattern-count optimization---'''
    distinct_pattern_count = configure_tertiary_pattern_count_stage(
        model,
        problem,
        pattern_variables,
        total_leftover,
        secondary_result.optimal_total_leftover,
    )
    tertiary_result = solve_tertiary_pattern_count_stage(
        model,
        distinct_pattern_count,
        adapter,
        options,
    )

    return extract_backend_output(
        pattern_variables,
        primary_result,
        secondary_result,
        tertiary_result,
        elapsed_time=elapsed_time,
    )
