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


# PuLP writes every coefficient, bound, and right-hand side to the CBC model
# file with 13 significant digits ("% .12e"), so a larger integer reaches
# CBC already rounded and the solved objective no longer matches the plan.
# 10**12 leaves a margin below that; it is also far below 2**53, where CBC's
# own double arithmetic stops being exact.
MAX_EXACT_MODEL_VALUE: Final[int] = 10**12


def _largest_model_value(problem: Problem) -> int:
    """Bound every integer the exact model can contain for ``problem``.

    Leftover coefficients are at most a stock length, and every right-hand
    side is a record quantity or a fixed stage optimum. A stage optimum is at
    most the total stock count or the total stock length, and the total
    stock length (at least the total stock count, as lengths are positive)
    also bounds each individual stock length.
    """
    total_stock_length = sum(stock.length * stock.quantity for stock in problem.stocks)
    largest_demand_quantity = max(
        (demand.quantity for demand in problem.demands), default=0,
    )
    return max(total_stock_length, largest_demand_quantity)


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
    iterations: int,
    proven_stock_count: int | None = None,
) -> UnverifiedBackendResult:
    # ``iterations`` is the number of backend solver invocations
    # (adapter.solve() calls) actually attempted for this solve() call --
    # not merely the number of stages proved complete. A stage stopped
    # before its own solve() was ever called (the shared deadline was
    # already spent) contributes 0 to this count; a stage whose solve()
    # was called and returned a non-terminal status still contributes 1,
    # even though it did not itself complete. Every caller below passes
    # the exact count for its own stop point so this field stays truthful
    # (see docs/formats.md, "Diagnostics").
    # Two situations guarantee pattern_variables already holds a complete,
    # constraint-satisfying assignment for the *whole* model (every demand
    # exactly met, every stock bound respected -- see
    # build_primary_stock_count_model), not just a partial or in-progress
    # one:
    #
    #   1. A FEASIBLE status: the adapter's own solve() call found and
    #      reported an integer incumbent before its allotted time ran out,
    #      even though it could not also prove that incumbent optimal.
    #
    #   2. backend_status is LIMIT_REACHED *and* completed_objective_stages
    #      is non-empty. LIMIT_REACHED is never produced by the adapter
    #      (see translate_pulp_status): the pipeline only constructs it
    #      itself, at the "the next stage's remaining budget is already
    #      <= 0" call sites. When that happens with at least one completed
    #      stage behind it, the *previous* stage already solved this same
    #      model to a proven optimum and the next stage's solve() was never
    #      even attempted -- so pattern_variables is exactly that previous
    #      stage's untouched, fully-solved state, not a guess. Per
    #      docs/resource-limits.md, "Earlier-stage incumbents remain
    #      eligible after a later stage reaches the time limit because
    #      fixing already-proved objective values preserves feasibility."
    #      (When completed_objective_stages is empty instead, the deadline
    #      ran out before any stage was ever solved, so no such state
    #      exists to extract.)
    #
    # Every other incomplete status (NOT_SOLVED, an adapter-side INFEASIBLE
    # at a later stage, UNKNOWN, ...) does not guarantee a solved model
    # state and must not expose model values.
    has_prior_stage_incumbent = (
        backend_status is BackendStatus.LIMIT_REACHED
        and len(completed_objective_stages) > 0
    )

    candidate = None
    reported_backend_status = backend_status
    if backend_status is BackendStatus.FEASIBLE or has_prior_stage_incumbent:
        candidate = extract_current_candidate(
            problem,
            pattern_variables,
        )
        # A verified prior-stage optimum is a genuine incumbent, just one
        # that is only proved for the completed prefix of the lexicographic
        # objective -- report it the same way solve() already reports any
        # unproved-but-available incumbent (BackendStatus.FEASIBLE), never
        # as LIMIT_REACHED, which by contract (see
        # docs/resource-limits.md, "Termination and incumbent rules") never
        # carries a candidate.
        reported_backend_status = BackendStatus.FEASIBLE

    return UnverifiedBackendResult(
        candidate=candidate,
        diagnostics=Diagnostics(
            termination_reason=_termination_reason(
                reported_backend_status, options,
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


def _backend_failure_result(
    *,
    patterns_generated: int,
    iterations: int,
    elapsed_time: float,
) -> UnverifiedBackendResult:
    """Return the structured BACKEND_FAILURE outcome for a stage whose own
    adapter.solve() call raised ``PulpAdapterError``.

    ``iterations`` is the number of solver invocations attempted up to and
    including the failing one (1 for a primary-stage failure, 2 for
    secondary, 3 for tertiary), matching the meaning documented for
    ``Diagnostics.iterations``. An unexpected backend failure makes no
    mathematical claim, so -- per docs/formats.md's error-diagnostics rules
    -- no objective stage is reported complete and both bounds are ``None``,
    even when an earlier stage in this same call did reach a proven optimum.
    """

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
            completed_objective_stages=(
                ObjectiveStage.STOCK_PIECE_COUNT,
            ),
            iterations=2,
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
                # solve_exact_enumerated_pipeline() catches PulpAdapterError
                # itself, per stage, and returns a structured BACKEND_FAILURE
                # result with an accurate `iterations` count -- it never lets
                # the exception propagate here.
                result = solve_exact_enumerated_pipeline(
                    problem,
                    enumeration.patterns,
                    self._adapter,
                    options,
                    deadline=deadline,
                )

        return replace(
            result,
            diagnostics=replace(
                result.diagnostics,
                elapsed_time=perf_counter() - started_at,
            ),
        )
