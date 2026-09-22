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
from saw.timing import Deadline


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


def _pattern_limit_result(patterns_generated: int) -> UnverifiedBackendResult:
    """Return the discarded-enumeration outcome for a pattern_limit hit.

    Per the pattern_limit row of docs/resource-limits.md, a limit hit claims
    no incumbent: the partial pattern set that tripped the limit is
    discarded in ``enumerate_patterns_for_problem`` and never reaches model
    construction, so it can never be optimized as if it were complete.
    """

    return UnverifiedBackendResult(
        candidate=None,
        diagnostics=Diagnostics(
            termination_reason=TerminationReason.PATTERN_LIMIT,
            backend_status=BackendStatus.LIMIT_REACHED,
            elapsed_time=0.0,
            patterns_generated=patterns_generated,
            iterations=0,
            completed_objective_stages=(),
            lower_bound=None,
            upper_bound=None,
        ),
    )


def _time_limit_enumeration_result(
    patterns_generated: int,
) -> UnverifiedBackendResult:
    """Return the discarded-enumeration outcome for a deadline hit mid-enumeration.

    Mirrors ``_pattern_limit_result``: per docs/resource-limits.md a partial
    pattern set is just as unsound to optimize over when the shared
    end-to-end deadline runs out as it is when ``pattern_limit`` is hit, so
    it is discarded the same way and never reaches model construction.
    Unlike ``_pattern_limit_result``, ``patterns_generated`` reports however
    many patterns had actually been produced when the deadline passed, not
    a configured ceiling (see the "End-to-end time" row of the termination
    table in docs/resource-limits.md).
    """

    return UnverifiedBackendResult(
        candidate=None,
        diagnostics=Diagnostics(
            termination_reason=TerminationReason.TIME_LIMIT,
            backend_status=BackendStatus.LIMIT_REACHED,
            elapsed_time=0.0,
            patterns_generated=patterns_generated,
            iterations=0,
            completed_objective_stages=(),
            lower_bound=None,
            upper_bound=None,
        ),
    )


def _remaining_stage_options(
    options: BackendOptions,
    deadline: Deadline,
) -> BackendOptions | None:
    """Return the options the next objective stage should receive, or
    ``None`` if its budget is already spent.

    Recomputes the remaining seconds on the shared ``deadline`` right
    before an expensive solver call, per docs/resource-limits.md ("One
    end-to-end time budget"): the secondary and tertiary stages must never
    receive the originally configured ``time_limit_seconds`` again, only
    whatever is left of it after validation, enumeration, model building,
    and any earlier stage. Returns ``None`` when the remainder is not
    positive, in which case the caller must not start that stage at all.
    """

    remaining = deadline.remaining_seconds()
    if remaining is not None and remaining <= 0:
        return None
    return replace(options, time_limit_seconds=remaining)


def solve_exact_enumerated_pipeline(
    problem: Problem,
    patterns: tuple[Pattern, ...],
    adapter: PulpCbcAdapter,
    options: BackendOptions,
    *,
    elapsed_time: float | None = None,
    deadline: Deadline | None = None,
) -> UnverifiedBackendResult:
    started_at = perf_counter()

    def current_elapsed_time() -> float:
        if elapsed_time is not None:
            return elapsed_time
        return perf_counter() - started_at

    # Per docs/resource-limits.md ("One end-to-end time budget"), every
    # stage below recomputes what remains of the shared deadline instead of
    # being handed options.time_limit_seconds -- the originally configured
    # value -- three times over. A caller that already owns a shared
    # Deadline (solve(), via ExactEnumeratedBackend.solve()) passes it in;
    # a caller that only has options.time_limit_seconds (a direct unit
    # test, or a bare call to this function) gets one anchored to this
    # pipeline's own start, which reproduces the pre-existing single-call
    # behaviour exactly.
    effective_deadline = (
        deadline
        if deadline is not None
        else Deadline.from_start(started_at, options.time_limit_seconds)
    )

    '''---stage 1: stock-count optimization---'''
    model, pattern_variables, stock_count = build_primary_stock_count_model(
        problem,
        patterns,
    )

    stage_1_options = _remaining_stage_options(options, effective_deadline)
    if stage_1_options is None:
        return _stopped_backend_result(
            problem,
            pattern_variables,
            BackendStatus.LIMIT_REACHED,
            options,
            elapsed_time=current_elapsed_time(),
            completed_objective_stages=(),
        )

    primary_result = solve_primary_stock_count_stage(
        model,
        stock_count,
        adapter,
        stage_1_options,
    )

    if primary_result.optimal_stock_count is None:
        return _stopped_backend_result(
            problem,
            pattern_variables,
            primary_result.backend_status,
            stage_1_options,
            elapsed_time=current_elapsed_time(),
            completed_objective_stages=(),
        )
    '''---stage 2: total-leftover optimization---'''

    stage_2_options = _remaining_stage_options(options, effective_deadline)
    if stage_2_options is None:
        return _stopped_backend_result(
            problem,
            pattern_variables,
            BackendStatus.LIMIT_REACHED,
            options,
            elapsed_time=current_elapsed_time(),
            completed_objective_stages=(
                ObjectiveStage.STOCK_PIECE_COUNT,
            ),
            proven_stock_count=primary_result.optimal_stock_count,
        )

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
        stage_2_options,
    )

    if secondary_result.optimal_total_leftover is None:
        return _stopped_backend_result(
            problem,
            pattern_variables,
            secondary_result.backend_status,
            stage_2_options,
            elapsed_time=current_elapsed_time(),
            completed_objective_stages=(
                ObjectiveStage.STOCK_PIECE_COUNT,
            ),
            proven_stock_count=primary_result.optimal_stock_count,
        )

    '''---stage 3: distinct-pattern-count optimization---'''
    stage_3_options = _remaining_stage_options(options, effective_deadline)
    if stage_3_options is None:
        return _stopped_backend_result(
            problem,
            pattern_variables,
            BackendStatus.LIMIT_REACHED,
            options,
            elapsed_time=current_elapsed_time(),
            completed_objective_stages=(
                ObjectiveStage.STOCK_PIECE_COUNT,
                ObjectiveStage.TOTAL_LEFTOVER,
            ),
            proven_stock_count=primary_result.optimal_stock_count,
        )

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
        stage_3_options,
    )

    if tertiary_result.optimal_distinct_pattern_count is None:
        return _stopped_backend_result(
            problem,
            pattern_variables,
            tertiary_result.backend_status,
            stage_3_options,
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
            supports_pattern_limit=True,
            objective_stages=frozenset(ObjectiveStage),
        )

    def check_availability(self) -> BackendAvailability:
        return self._adapter.check_availability()

    def check_support(
        self,
        problem: Problem,
        options: BackendOptions,
    ) -> BackendSupport:
        return BackendSupport(supported=True)

    def solve(
        self,
        problem: Problem,
        options: BackendOptions,
    ) -> UnverifiedBackendResult:
        started_at = perf_counter()

        # Prefer the shared deadline solve() already anchored (it covers
        # validation and preflight checks too); a caller that bypasses
        # solve() and only sets time_limit_seconds gets one anchored here,
        # which reproduces the pre-existing single-call behaviour exactly.
        deadline = (
            options.deadline
            if options.deadline is not None
            else Deadline.from_start(started_at, options.time_limit_seconds)
        )

        if not problem.demands:
            result = _empty_demand_result()
        else:
            enumeration = enumerate_patterns_for_problem(
                problem,
                options.pattern_limit,
                deadline=deadline,
            )

            if enumeration.deadline_expired:
                result = _time_limit_enumeration_result(
                    enumeration.patterns_generated
                )
            elif enumeration.limit_reached:
                result = _pattern_limit_result(enumeration.patterns_generated)
            elif not enumeration.patterns:
                result = _no_pattern_infeasible_result()
            else:
                try:
                    result = solve_exact_enumerated_pipeline(
                        problem,
                        enumeration.patterns,
                        self._adapter,
                        options,
                        deadline=deadline,
                    )
                except PulpAdapterError:
                    return UnverifiedBackendResult(
                        candidate=None,
                        diagnostics=Diagnostics(
                            termination_reason=TerminationReason.BACKEND_FAILURE,
                            backend_status=BackendStatus.FAILED,
                            elapsed_time=perf_counter() - started_at,
                            patterns_generated=len(enumeration.patterns),
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
