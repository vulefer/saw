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
    UnverifiedBackendResult,
    UnverifiedCandidate,
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

# pulp writes numbers into cbc's model file with 13 significant digits,
# so bigger integers get rounded and the result stops matching the plan
# 10**12 leaves some margin, and is far below 2**53 where cbc's float math stops being exact
MAX_EXACT_MODEL_VALUE: Final[int] = 10**12


# the function computes the largest possible integer that can appear in the
# problem, we later compare it with the MAX_EXACT_MODEL_VALUE so no number gets rounded
def _largest_model_value(problem: Problem) -> int:
    total_stock_length = sum(stock.length * stock.quantity for stock in problem.stocks)
    largest_demand_quantity = max(
        (demand.quantity for demand in problem.demands),
        default=0,
    )
    return max(total_stock_length, largest_demand_quantity)


# picks the termination reason that matches the backend status
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


# builds the result when the pipeline stops before finishing all 3 stages
# iterations is how many solver calls were actually made, a stage stopped
# before its solve() adds 0, see docs/formats.md "Diagnostics"
def _stopped_backend_result(
    problem: Problem,
    pattern_variables: dict[Pattern, pulp.LpVariable],
    backend_status: BackendStatus,
    options: BackendOptions,
    *,
    elapsed_time: float,
    completed_objective_stages: tuple[ObjectiveStage, ...],
    iterations: int,
    proven_stock_count: int | None = None,
) -> UnverifiedBackendResult:
    # the model only holds a complete valid solution in two cases:
    # - FEASIBLE: cbc found a solution but couldnt prove it optimal in time
    # - LIMIT_REACHED after at least one finished stage: time ran out before the
    #   next stage started, so the model still holds the previous stage's optimum
    # any other status could leave the model half solved, so no candidate is taken
    # LIMIT_REACHED only comes from this pipeline, never from pulp
    has_prior_stage_incumbent = (
        backend_status is BackendStatus.LIMIT_REACHED and len(completed_objective_stages) > 0
    )

    candidate = None
    reported_backend_status = backend_status
    if backend_status is BackendStatus.FEASIBLE or has_prior_stage_incumbent:
        candidate = extract_current_candidate(
            problem,
            pattern_variables,
        )
        # an earlier stage's optimum is reported as FEASIBLE, since
        # LIMIT_REACHED never carries a candidate, see docs/resource-limits.md
        reported_backend_status = BackendStatus.FEASIBLE

    return UnverifiedBackendResult(
        candidate=candidate,
        diagnostics=Diagnostics(
            termination_reason=_termination_reason(
                reported_backend_status,
                options,
            ),
            backend_status=reported_backend_status,
            elapsed_time=elapsed_time,
            patterns_generated=len(pattern_variables),
            iterations=iterations,
            completed_objective_stages=completed_objective_stages,
            lower_bound=proven_stock_count,
            upper_bound=(
                proven_stock_count
                if proven_stock_count is not None
                else (
                    candidate.objective_value.stock_piece_count if candidate is not None else None
                )
            ),
        ),
    )


# nothing to cut, so using no stock is the proven optimum
def _empty_demand_result() -> UnverifiedBackendResult:
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


# no demanded piece fits on any stock, so the problem is proven infeasible
def _no_pattern_infeasible_result() -> UnverifiedBackendResult:
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


# result when pattern_limit was hit, the partial patterns were thrown away
# so nothing was solved and there is no candidate
def _pattern_limit_result(patterns_generated: int) -> UnverifiedBackendResult:
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


# same as _pattern_limit_result but for the deadline running out during enumeration
# patterns_generated is how many were made before time ran out, not a limit
def _time_limit_enumeration_result(
    patterns_generated: int,
) -> UnverifiedBackendResult:
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


# result when a stage's solver call raised PulpAdapterError
# iterations includes the failed call (1, 2 or 3 depending on the stage)
# a failure proves nothing, so no stage counts as complete and both bounds are None,
# even if an earlier stage did finish
def _backend_failure_result(
    *,
    patterns_generated: int,
    iterations: int,
    elapsed_time: float,
) -> UnverifiedBackendResult:
    return UnverifiedBackendResult(
        candidate=None,
        diagnostics=Diagnostics(
            termination_reason=TerminationReason.BACKEND_FAILURE,
            backend_status=BackendStatus.FAILED,
            elapsed_time=elapsed_time,
            patterns_generated=patterns_generated,
            iterations=iterations,
            completed_objective_stages=(),
            lower_bound=None,
            upper_bound=None,
        ),
    )


# options for the next stage, with time_limit_seconds set to the time left
# on the deadline, None if no time is left and the stage must not start
def _remaining_stage_options(
    options: BackendOptions,
    deadline: Deadline,
) -> BackendOptions | None:
    remaining = deadline.remaining_seconds()
    if remaining is not None and remaining <= 0:
        return None
    return replace(options, time_limit_seconds=remaining)


# runs the 3 optimization stages in order, each one keeping the previous optimum fixed
# stops early with a partial result if time runs out or the solver fails
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

    # each stage gets the time left on the shared deadline, not the full time_limit_seconds
    # if no deadline is passed in (e.g. in tests), one is started here
    effective_deadline = (
        deadline
        if deadline is not None
        else Deadline.from_start(started_at, options.time_limit_seconds)
    )

    # --- stage 1: stock-count optimization ---
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
            iterations=0,
        )

    try:
        primary_result = solve_primary_stock_count_stage(
            model,
            stock_count,
            adapter,
            stage_1_options,
        )
    except PulpAdapterError:
        return _backend_failure_result(
            patterns_generated=len(pattern_variables),
            iterations=1,
            elapsed_time=current_elapsed_time(),
        )

    if primary_result.optimal_stock_count is None:
        return _stopped_backend_result(
            problem,
            pattern_variables,
            primary_result.backend_status,
            stage_1_options,
            elapsed_time=current_elapsed_time(),
            completed_objective_stages=(),
            iterations=1,
        )

    # --- stage 2: total-leftover optimization ---
    stage_2_options = _remaining_stage_options(options, effective_deadline)
    if stage_2_options is None:
        return _stopped_backend_result(
            problem,
            pattern_variables,
            BackendStatus.LIMIT_REACHED,
            options,
            elapsed_time=current_elapsed_time(),
            completed_objective_stages=(ObjectiveStage.STOCK_PIECE_COUNT,),
            iterations=1,
            proven_stock_count=primary_result.optimal_stock_count,
        )

    total_leftover = configure_secondary_leftover_stage(
        model,
        problem,
        pattern_variables,
        stock_count,
        primary_result.optimal_stock_count,
    )
    try:
        secondary_result = solve_secondary_leftover_stage(
            model,
            total_leftover,
            adapter,
            stage_2_options,
        )
    except PulpAdapterError:
        return _backend_failure_result(
            patterns_generated=len(pattern_variables),
            iterations=2,
            elapsed_time=current_elapsed_time(),
        )

    if secondary_result.optimal_total_leftover is None:
        return _stopped_backend_result(
            problem,
            pattern_variables,
            secondary_result.backend_status,
            stage_2_options,
            elapsed_time=current_elapsed_time(),
            completed_objective_stages=(ObjectiveStage.STOCK_PIECE_COUNT,),
            iterations=2,
            proven_stock_count=primary_result.optimal_stock_count,
        )

    # --- stage 3: distinct-pattern-count optimization ---
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
            iterations=2,
            proven_stock_count=primary_result.optimal_stock_count,
        )

    distinct_pattern_count = configure_tertiary_pattern_count_stage(
        model,
        problem,
        pattern_variables,
        total_leftover,
        secondary_result.optimal_total_leftover,
    )
    try:
        tertiary_result = solve_tertiary_pattern_count_stage(
            model,
            distinct_pattern_count,
            adapter,
            stage_3_options,
        )
    except PulpAdapterError:
        return _backend_failure_result(
            patterns_generated=len(pattern_variables),
            iterations=3,
            elapsed_time=current_elapsed_time(),
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
            iterations=3,
            proven_stock_count=primary_result.optimal_stock_count,
        )

    return extract_backend_output(
        pattern_variables,
        primary_result,
        secondary_result,
        tertiary_result,
        elapsed_time=current_elapsed_time(),
    )


# backend that lists every cutting pattern and solves over them exactly with cbc
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

    # rejects problems with numbers too big for cbc's model file
    def check_support(
        self,
        problem: Problem,
        options: BackendOptions,
    ) -> BackendSupport:
        largest_model_value = _largest_model_value(problem)
        if largest_model_value > MAX_EXACT_MODEL_VALUE:
            return BackendSupport(
                supported=False,
                reason=(
                    "The model needs the integer value "
                    f"{largest_model_value}, but the CBC model file only "
                    f"represents integers up to {MAX_EXACT_MODEL_VALUE} "
                    "exactly."
                ),
            )
        return BackendSupport(supported=True)

    def solve(
        self,
        problem: Problem,
        options: BackendOptions,
    ) -> UnverifiedBackendResult:
        started_at = perf_counter()

        # use the deadline from solve() if there is one, otherwise start one here
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
                result = _time_limit_enumeration_result(enumeration.patterns_generated)
            elif enumeration.limit_reached:
                result = _pattern_limit_result(enumeration.patterns_generated)
            elif not enumeration.patterns:
                result = _no_pattern_infeasible_result()
            else:
                # the pipeline catches PulpAdapterError itself, so it never reaches here
                result = solve_exact_enumerated_pipeline(
                    problem,
                    enumeration.patterns,
                    self._adapter,
                    options,
                    deadline=deadline,
                )

        # overwrite elapsed_time so it covers the whole backend call, including enumeration
        return replace(
            result,
            diagnostics=replace(
                result.diagnostics,
                elapsed_time=perf_counter() - started_at,
            ),
        )
