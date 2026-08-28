from __future__ import annotations

from dataclasses import replace
from time import perf_counter
from typing import Final

import pulp

from saw.backends.base import (
    BackendAvailability,
    BackendCapabilities,
    BackendOptions,
    BackendSupport,
    UnverifiedCandidate,
    UnverifiedBackendResult,
)
from saw.backends.exact_enumerated.output import (
    extract_backend_output,
    extract_current_candidate,
)
from saw.backends.exact_enumerated.stages import (
    build_primary_stock_count_model,
    configure_secondary_leftover_stage,
    configure_tertiary_pattern_count_stage,
    solve_primary_stock_count_stage,
    solve_secondary_leftover_stage,
    solve_tertiary_pattern_count_stage,
)
from saw.backends.pulp_backend import PulpAdapterError, PulpCbcAdapter
from saw.models import (
    BackendStatus,
    Diagnostics,
    ObjectiveStage,
    ObjectiveValue,
    Problem,
    TerminationReason,
)
from saw.patterns import Pattern, enumerate_patterns_for_problem


def _termination_reason(
    backend_status: BackendStatus,
    options: BackendOptions,
) -> TerminationReason:
    if backend_status is BackendStatus.INFEASIBLE:
        return TerminationReason.COMPLETED
    if (
        backend_status
        in {
            BackendStatus.FEASIBLE,
            BackendStatus.LIMIT_REACHED,
            BackendStatus.NOT_SOLVED,
        }
        and options.time_limit_seconds is not None
    ):
        return TerminationReason.TIME_LIMIT
    if backend_status in {
        BackendStatus.FAILED,
        BackendStatus.NOT_RUN,
        BackendStatus.NOT_SOLVED,
        BackendStatus.UNBOUNDED,
    }:
        return TerminationReason.BACKEND_FAILURE
    return TerminationReason.UNKNOWN_BACKEND_STATUS


def _stopped_backend_result(
    problem: Problem,
    pattern_variables: dict[Pattern, pulp.LpVariable],
    backend_status: BackendStatus,
    options: BackendOptions,
    *,
    elapsed_time: float,
    completed_objective_stages: tuple[ObjectiveStage, ...],
    proven_stock_count: int | None = None,
) -> UnverifiedBackendResult:
    # A FEASIBLE status explicitly guarantees an integer incumbent. Other
    # incomplete statuses do not, so they must not expose model values.
    candidate = None
    if backend_status is BackendStatus.FEASIBLE:
        candidate = extract_current_candidate(
            problem,
            pattern_variables,
        )

    return UnverifiedBackendResult(
        candidate=candidate,
        diagnostics=Diagnostics(
            termination_reason=_termination_reason(backend_status, options),
            backend_status=backend_status,
            elapsed_time=elapsed_time,
            patterns_generated=len(pattern_variables),
            iterations=1,
            completed_objective_stages=completed_objective_stages,
            lower_bound=proven_stock_count,
            upper_bound=(
                proven_stock_count
                if proven_stock_count is not None
                else (
                    candidate.objective_value.stock_piece_count
                    if candidate is not None
                    else None
                )
            ),
        ),
    )


def _empty_demand_result() -> UnverifiedBackendResult:
    """Return the proven optimum for a problem that requires no cuts."""

    return UnverifiedBackendResult(
        candidate=UnverifiedCandidate(
            cut_patterns=(),
            objective_value=ObjectiveValue(
                stock_piece_count=0,
                total_leftover=0,
                distinct_pattern_count=0,
            ),
        ),
        diagnostics=Diagnostics(
            termination_reason=TerminationReason.COMPLETED,
            backend_status=BackendStatus.OPTIMAL,
            elapsed_time=0.0,
            patterns_generated=0,
            iterations=0,
            completed_objective_stages=tuple(ObjectiveStage),
            lower_bound=0,
            upper_bound=0,
        ),
    )


def _no_pattern_infeasible_result() -> UnverifiedBackendResult:
    """Return proven infeasibility when no non-empty cut can be made."""

    return UnverifiedBackendResult(
        candidate=None,
        diagnostics=Diagnostics(
            termination_reason=TerminationReason.COMPLETED,
            backend_status=BackendStatus.INFEASIBLE,
            elapsed_time=0.0,
            patterns_generated=0,
            iterations=0,
            completed_objective_stages=(),
            lower_bound=None,
            upper_bound=None,
        ),
    )


def solve_exact_enumerated_pipeline(
    problem: Problem,
    patterns: tuple[Pattern, ...],
    adapter: PulpCbcAdapter,
    options: BackendOptions,
    *,
    elapsed_time: float | None = None,
) -> UnverifiedBackendResult:
    started_at = perf_counter()

    def current_elapsed_time() -> float:
        if elapsed_time is not None:
            return elapsed_time
        return perf_counter() - started_at

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
        return _stopped_backend_result(
            problem,
            pattern_variables,
            primary_result.backend_status,
            options,
            elapsed_time=current_elapsed_time(),
            completed_objective_stages=(),
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
        return _stopped_backend_result(
            problem,
            pattern_variables,
            secondary_result.backend_status,
            options,
            elapsed_time=current_elapsed_time(),
            completed_objective_stages=(
                ObjectiveStage.STOCK_PIECE_COUNT,
            ),
            proven_stock_count=primary_result.optimal_stock_count,
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

    if tertiary_result.optimal_distinct_pattern_count is None:
        return _stopped_backend_result(
            problem,
            pattern_variables,
            tertiary_result.backend_status,
            options,
            elapsed_time=current_elapsed_time(),
            completed_objective_stages=(
                ObjectiveStage.STOCK_PIECE_COUNT,
                ObjectiveStage.TOTAL_LEFTOVER,
            ),
            proven_stock_count=primary_result.optimal_stock_count,
        )

    return extract_backend_output(
        pattern_variables,
        primary_result,
        secondary_result,
        tertiary_result,
        elapsed_time=current_elapsed_time(),
    )


class ExactEnumeratedBackend:

    _BACKEND_ID: Final[str] = "exact_enumerated"

    def __init__(self, adapter: PulpCbcAdapter | None = None) -> None:
        self._adapter = adapter or PulpCbcAdapter()

    @property
    def backend_id(self) -> str:
        return self._BACKEND_ID

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            proves_optimality=True,
            proves_infeasibility=True,
            supports_time_limit=True,
            supports_pattern_limit=False,
            objective_stages=frozenset(ObjectiveStage),
        )

    def check_availability(self) -> BackendAvailability:
        return self._adapter.check_availability()

    def check_support(
        self,
        problem: Problem,
        options: BackendOptions,
    ) -> BackendSupport:
        if options.pattern_limit is not None:
            return BackendSupport(
                supported=False,
                reason=(
                    "The exact enumerated backend does not currently support "
                    "a pattern limit."
                ),
            )
        return BackendSupport(supported=True)

    def solve(
        self,
        problem: Problem,
        options: BackendOptions,
    ) -> UnverifiedBackendResult:
        started_at = perf_counter()
        patterns = enumerate_patterns_for_problem(problem)

        if not problem.demands:
            result = _empty_demand_result()
        elif not patterns:
            result = _no_pattern_infeasible_result()
        else:
            try:
                result = solve_exact_enumerated_pipeline(
                    problem,
                    patterns,
                    self._adapter,
                    options,
                )
            except PulpAdapterError:
                return UnverifiedBackendResult(
                    candidate=None,
                    diagnostics=Diagnostics(
                        termination_reason=TerminationReason.BACKEND_FAILURE,
                        backend_status=BackendStatus.FAILED,
                        elapsed_time=perf_counter() - started_at,
                        patterns_generated=len(patterns),
                        iterations=1,
                        completed_objective_stages=(),
                        lower_bound=None,
                        upper_bound=None,
                    ),
                )

        return replace(
            result,
            diagnostics=replace(
                result.diagnostics,
                elapsed_time=perf_counter() - started_at,
            ),
        )
