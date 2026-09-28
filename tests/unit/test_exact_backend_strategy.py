from __future__ import annotations

import pulp
import pytest

from saw import Demand, Problem, Stock
from saw.backends.base import (
    BackendAvailability,
    BackendOptions,
    OptimizationBackend,
)
from saw.backends.exact_enumerated import (
    ExactEnumeratedBackend,
    solve_exact_enumerated_pipeline,
)
import saw.backends.exact_enumerated.backend as backend_module
from saw.backends.pulp_backend import PulpAdapterError
from saw.models import (
    BackendStatus,
    ObjectiveStage,
    ObjectiveValue,
    TerminationReason,
)
from saw.patterns import enumerate_patterns_for_problem
import saw.timing as timing_module
from saw.timing import Deadline


class FakeClock:
    """A controllable, manually-advanced stand-in for time.perf_counter().

    Deadline-sharing tests monkeypatch both the pipeline's own
    ``perf_counter`` and ``saw.timing``'s with the SAME instance, so a
    simulated "the solver took N seconds" advance is visible consistently
    to whichever module reads the clock next. This makes the timing
    assertions exact and repeatable instead of depending on real
    wall-clock speed.
    """

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeAdapter:
    def __init__(
        self,
        status: BackendStatus = BackendStatus.OPTIMAL,
        *,
        error: Exception | None = None,
    ) -> None:
        self.status = status
        self.error = error
        self.solve_calls = 0

    def check_availability(self) -> BackendAvailability:
        return BackendAvailability(available=True, backend_version="test")

    def solve(
        self,
        model: pulp.LpProblem,
        options: BackendOptions,
    ) -> BackendStatus:
        self.solve_calls += 1
        if self.error is not None:
            raise self.error
        return self.status


class FeasibleIncumbentAdapter(FakeAdapter):
    def solve(
        self,
        model: pulp.LpProblem,
        options: BackendOptions,
    ) -> BackendStatus:
        for variable in model.variables():
            variable.varValue = 1
        return BackendStatus.FEASIBLE


class ClockAdvancingAdapter(FakeAdapter):
    """A FakeAdapter that simulates spending time on every solve() call.

    Always reports an OPTIMAL, fully-set incumbent (suitable for a
    single-pattern problem, where every model variable can simply be set to
    1) so a full three-stage pipeline run can be walked to completion while
    recording exactly what BackendOptions each stage received.
    """

    def __init__(self, clock: FakeClock, seconds_per_call: float) -> None:
        super().__init__(BackendStatus.OPTIMAL)
        self._clock = clock
        self._seconds_per_call = seconds_per_call
        self.received_options: list[BackendOptions] = []

    def solve(
        self,
        model: pulp.LpProblem,
        options: BackendOptions,
    ) -> BackendStatus:
        self.solve_calls += 1
        self.received_options.append(options)
        self._clock.advance(self._seconds_per_call)
        for variable in model.variables():
            variable.varValue = 1
        return self.status


def _one_piece_problem() -> Problem:
    return Problem(
        stocks=(Stock("stock-10", "steel", 10, 1),),
        demands=(Demand("piece-6", "steel", 6, 1),),
    )


def _multi_pattern_problem() -> Problem:
    # Enumerates to exactly 3 non-empty patterns: {demand-4: 1},
    # {demand-6: 1}, and {demand-4: 1, demand-6: 1}.
    return Problem(
        stocks=(Stock("stock-10", "steel", 10, 1),),
        demands=(
            Demand("piece-6", "steel", 6, 1),
            Demand("piece-4", "steel", 4, 1),
        ),
    )


def test_exact_backend_is_an_optimization_strategy() -> None:
    backend = ExactEnumeratedBackend(FakeAdapter())  # type: ignore[arg-type]

    assert isinstance(backend, OptimizationBackend)
    assert backend.backend_id == "exact_enumerated"
    assert backend.check_availability().available is True
    assert backend.capabilities().objective_stages == frozenset(ObjectiveStage)


def test_exact_backend_reports_supported_pattern_limit() -> None:
    backend = ExactEnumeratedBackend(FakeAdapter())  # type: ignore[arg-type]

    assert backend.capabilities().supports_pattern_limit is True

    support = backend.check_support(
        _one_piece_problem(),
        BackendOptions(pattern_limit=10),
    )

    assert support.supported is True


def test_primary_infeasibility_is_returned_as_a_structured_result() -> None:
    problem = _one_piece_problem()
    patterns = enumerate_patterns_for_problem(problem).patterns

    result = solve_exact_enumerated_pipeline(
        problem,
        patterns,
        FakeAdapter(BackendStatus.INFEASIBLE),  # type: ignore[arg-type]
        BackendOptions(),
        elapsed_time=1.5,
    )

    assert result.candidate is None
    assert result.diagnostics.backend_status is BackendStatus.INFEASIBLE
    assert result.diagnostics.termination_reason is TerminationReason.COMPLETED
    assert result.diagnostics.elapsed_time == 1.5
    assert result.diagnostics.completed_objective_stages == ()


def test_empty_demand_has_a_proven_zero_optimum_without_calling_solver() -> None:
    adapter = FakeAdapter()
    backend = ExactEnumeratedBackend(adapter)  # type: ignore[arg-type]
    problem = Problem(
        stocks=(Stock("stock-10", "steel", 10, 1),),
        demands=(),
    )

    result = backend.solve(problem, BackendOptions())

    assert result.candidate is not None
    assert result.candidate.cut_patterns == ()
    assert result.candidate.objective_value == ObjectiveValue(0, 0, 0)
    assert result.diagnostics.backend_status is BackendStatus.OPTIMAL
    assert result.diagnostics.termination_reason is TerminationReason.COMPLETED
    assert result.diagnostics.patterns_generated == 0
    assert result.diagnostics.iterations == 0
    assert result.diagnostics.completed_objective_stages == tuple(ObjectiveStage)
    assert result.diagnostics.lower_bound == 0
    assert result.diagnostics.upper_bound == 0
    assert adapter.solve_calls == 0


def test_no_feasible_patterns_proves_infeasibility_without_solver() -> None:
    adapter = FakeAdapter()
    backend = ExactEnumeratedBackend(adapter)  # type: ignore[arg-type]
    problem = Problem(
        stocks=(Stock("stock-3", "steel", 3, 1),),
        demands=(Demand("piece-4", "steel", 4, 1),),
    )

    result = backend.solve(problem, BackendOptions())

    assert result.candidate is None
    assert result.diagnostics.backend_status is BackendStatus.INFEASIBLE
    assert result.diagnostics.termination_reason is TerminationReason.COMPLETED
    assert result.diagnostics.patterns_generated == 0
    assert result.diagnostics.iterations == 0
    assert result.diagnostics.completed_objective_stages == ()
    assert result.diagnostics.lower_bound is None
    assert result.diagnostics.upper_bound is None
    assert adapter.solve_calls == 0


def test_feasible_limit_incumbent_is_returned_without_optimality_claim() -> None:
    problem = _one_piece_problem()
    patterns = enumerate_patterns_for_problem(problem).patterns

    result = solve_exact_enumerated_pipeline(
        problem,
        patterns,
        FeasibleIncumbentAdapter(),  # type: ignore[arg-type]
        BackendOptions(time_limit_seconds=0.1),
        elapsed_time=0.1,
    )

    assert result.candidate is not None
    assert result.candidate.objective_value.stock_piece_count == 1
    assert result.candidate.objective_value.total_leftover == 4
    assert result.candidate.objective_value.distinct_pattern_count == 1
    assert result.diagnostics.backend_status is BackendStatus.FEASIBLE
    assert result.diagnostics.termination_reason is TerminationReason.TIME_LIMIT
    assert result.diagnostics.completed_objective_stages == ()


def test_adapter_failure_becomes_a_structured_backend_error() -> None:
    adapter = FakeAdapter(error=PulpAdapterError("solver failed"))
    backend = ExactEnumeratedBackend(adapter)  # type: ignore[arg-type]

    result = backend.solve(_one_piece_problem(), BackendOptions())

    assert result.candidate is None
    assert result.diagnostics.backend_status is BackendStatus.FAILED
    assert result.diagnostics.termination_reason is (TerminationReason.BACKEND_FAILURE)
    # A primary-stage failure means exactly one solver call was attempted;
    # see docs/formats.md, "Diagnostics", for what `iterations` counts.
    assert result.diagnostics.iterations == 1
    assert result.diagnostics.completed_objective_stages == ()
    assert result.diagnostics.lower_bound is None
    assert result.diagnostics.upper_bound is None
    assert adapter.solve_calls == 1


class StageFailureAdapter(FakeAdapter):
    """Succeeds (OPTIMAL, every model variable set to 1) on every call up to
    but not including ``fail_at_call``, then raises ``PulpAdapterError`` on
    that call -- so a test can put the failure at a chosen lexicographic
    stage after some number of earlier stages already proved optimal.
    """

    def __init__(self, fail_at_call: int) -> None:
        super().__init__(BackendStatus.OPTIMAL)
        self._fail_at_call = fail_at_call

    def solve(
        self,
        model: pulp.LpProblem,
        options: BackendOptions,
    ) -> BackendStatus:
        self.solve_calls += 1
        if self.solve_calls == self._fail_at_call:
            raise PulpAdapterError("solver failed")
        for variable in model.variables():
            variable.varValue = 1
        return self.status


def test_secondary_stage_adapter_failure_reports_two_attempted_iterations() -> None:
    """A failure on the *second* solver call must report `iterations == 2`,
    not the `1` a naive hardcoded value would give every backend failure
    regardless of which stage actually failed.
    """
    problem = _one_piece_problem()
    patterns = enumerate_patterns_for_problem(problem).patterns
    adapter = StageFailureAdapter(fail_at_call=2)

    result = solve_exact_enumerated_pipeline(
        problem,
        patterns,
        adapter,  # type: ignore[arg-type]
        BackendOptions(),
        elapsed_time=0.0,
    )

    assert result.candidate is None
    assert result.diagnostics.backend_status is BackendStatus.FAILED
    assert result.diagnostics.termination_reason is (TerminationReason.BACKEND_FAILURE)
    assert result.diagnostics.iterations == 2
    # An unexpected backend failure makes no mathematical claim, so the
    # primary stage's already-proved optimum is not reported as completed
    # here, and both bounds stay null (see docs/formats.md's error rules).
    assert result.diagnostics.completed_objective_stages == ()
    assert result.diagnostics.lower_bound is None
    assert result.diagnostics.upper_bound is None
    assert adapter.solve_calls == 2


def test_tertiary_stage_adapter_failure_reports_three_attempted_iterations() -> None:
    problem = _one_piece_problem()
    patterns = enumerate_patterns_for_problem(problem).patterns
    adapter = StageFailureAdapter(fail_at_call=3)

    result = solve_exact_enumerated_pipeline(
        problem,
        patterns,
        adapter,  # type: ignore[arg-type]
        BackendOptions(),
        elapsed_time=0.0,
    )

    assert result.candidate is None
    assert result.diagnostics.backend_status is BackendStatus.FAILED
    assert result.diagnostics.termination_reason is (TerminationReason.BACKEND_FAILURE)
    assert result.diagnostics.iterations == 3
    assert result.diagnostics.completed_objective_stages == ()
    assert result.diagnostics.lower_bound is None
    assert result.diagnostics.upper_bound is None
    assert adapter.solve_calls == 3


# --- pattern_limit ---------------------------------------------------------


def test_pattern_limit_exactly_matching_total_reaches_the_solver() -> None:
    adapter = FakeAdapter(BackendStatus.INFEASIBLE)
    backend = ExactEnumeratedBackend(adapter)  # type: ignore[arg-type]

    result = backend.solve(
        _multi_pattern_problem(),
        BackendOptions(pattern_limit=3),
    )

    assert adapter.solve_calls == 1
    assert result.diagnostics.termination_reason is TerminationReason.COMPLETED
    assert result.diagnostics.backend_status is BackendStatus.INFEASIBLE
    assert result.diagnostics.patterns_generated == 3


def test_pattern_limit_hit_returns_limit_with_accurate_count_and_skips_the_solver() -> None:
    adapter = FakeAdapter()
    backend = ExactEnumeratedBackend(adapter)  # type: ignore[arg-type]

    # The problem enumerates to 3 patterns; a limit of 2 is hit on the 3rd.
    result = backend.solve(
        _multi_pattern_problem(),
        BackendOptions(pattern_limit=2),
    )

    assert result.candidate is None
    assert result.diagnostics.termination_reason is TerminationReason.PATTERN_LIMIT
    assert result.diagnostics.backend_status is BackendStatus.LIMIT_REACHED
    assert result.diagnostics.patterns_generated == 2
    assert result.diagnostics.iterations == 0
    assert result.diagnostics.completed_objective_stages == ()
    assert result.diagnostics.lower_bound is None
    assert result.diagnostics.upper_bound is None
    assert adapter.solve_calls == 0


def test_pattern_limit_hit_is_deterministic_across_repeated_backend_calls() -> None:
    problem = _multi_pattern_problem()

    first = ExactEnumeratedBackend(FakeAdapter()).solve(  # type: ignore[arg-type]
        problem,
        BackendOptions(pattern_limit=2),
    )
    second = ExactEnumeratedBackend(FakeAdapter()).solve(  # type: ignore[arg-type]
        problem,
        BackendOptions(pattern_limit=2),
    )

    assert first.diagnostics.backend_status == second.diagnostics.backend_status
    assert first.diagnostics.termination_reason == second.diagnostics.termination_reason
    assert first.diagnostics.patterns_generated == second.diagnostics.patterns_generated


# --- one shared end-to-end time budget -------------------------------------
#
# See docs/resource-limits.md, "One end-to-end time budget": time_limit_seconds
# is one deadline for the whole solve, not a fresh allowance re-issued to
# each of the three lexicographic objective stages. These tests use a fully
# controllable FakeClock (never real sleeping) so "how much time is left"
# can be asserted exactly instead of with a flaky real-time race.


def test_each_stage_receives_only_what_remains_of_the_shared_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    problem = _one_piece_problem()
    patterns = enumerate_patterns_for_problem(problem).patterns
    clock = FakeClock(start=100.0)
    monkeypatch.setattr(backend_module, "perf_counter", clock)
    monkeypatch.setattr(timing_module, "perf_counter", clock)
    adapter = ClockAdvancingAdapter(clock, seconds_per_call=2.0)

    result = solve_exact_enumerated_pipeline(
        problem,
        patterns,
        adapter,  # type: ignore[arg-type]
        BackendOptions(time_limit_seconds=10.0),
        deadline=Deadline.from_start(started_at=clock.now, time_limit_seconds=10.0),
    )

    assert len(adapter.received_options) == 3
    stage_limits = [options.time_limit_seconds for options in adapter.received_options]
    # Each stage receives strictly less than the one before it -- proof the
    # 10-second budget is shared across the three solver calls rather than
    # being handed to each of them in full (which would let a
    # time_limit_seconds=10 configuration consume up to 30 seconds).
    assert stage_limits == pytest.approx([10.0, 8.0, 6.0])
    assert result.diagnostics.backend_status is BackendStatus.OPTIMAL
    assert result.diagnostics.completed_objective_stages == tuple(ObjectiveStage)


def test_pipeline_anchors_its_own_deadline_when_none_is_shared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A direct call with only options.time_limit_seconds (no shared
    Deadline) must behave exactly as it did before deadline-sharing was
    introduced: each stage still gets a live, positive remaining-time
    figure derived from that same configured value, anchored to this
    pipeline call's own start.
    """
    problem = _one_piece_problem()
    patterns = enumerate_patterns_for_problem(problem).patterns
    clock = FakeClock(start=0.0)
    monkeypatch.setattr(backend_module, "perf_counter", clock)
    monkeypatch.setattr(timing_module, "perf_counter", clock)
    adapter = ClockAdvancingAdapter(clock, seconds_per_call=1.0)

    result = solve_exact_enumerated_pipeline(
        problem,
        patterns,
        adapter,  # type: ignore[arg-type]
        BackendOptions(time_limit_seconds=10.0),
    )

    stage_limits = [options.time_limit_seconds for options in adapter.received_options]
    assert stage_limits == pytest.approx([10.0, 9.0, 8.0])
    assert result.diagnostics.backend_status is BackendStatus.OPTIMAL


def test_deadline_exhausted_after_a_stage_returns_the_prior_stage_incumbent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """docs/resource-limits.md: "Earlier-stage incumbents remain eligible
    after a later stage reaches the time limit because fixing
    already-proved objective values preserves feasibility." Stage 1 solves
    this single-pattern problem to a proven optimum; stage 2 is never even
    attempted because the shared deadline is already spent. The complete,
    constraint-satisfying assignment stage 1 left behind must still be
    returned -- honestly, as an unproven-past-that-prefix FEASIBLE
    incumbent, never silently upgraded to LIMIT_REACHED-with-no-plan.
    """
    problem = _one_piece_problem()
    patterns = enumerate_patterns_for_problem(problem).patterns
    clock = FakeClock(start=0.0)
    monkeypatch.setattr(backend_module, "perf_counter", clock)
    monkeypatch.setattr(timing_module, "perf_counter", clock)
    # Stage 1 alone consumes the entire configured 5-second budget.
    adapter = ClockAdvancingAdapter(clock, seconds_per_call=5.0)

    result = solve_exact_enumerated_pipeline(
        problem,
        patterns,
        adapter,  # type: ignore[arg-type]
        BackendOptions(time_limit_seconds=5.0),
        deadline=Deadline.from_start(started_at=clock.now, time_limit_seconds=5.0),
    )

    # Stage 2 must never be started: its options.time_limit_seconds would
    # already be <= 0.
    assert adapter.solve_calls == 1
    assert result.candidate is not None
    assert result.candidate.objective_value.stock_piece_count == 1
    assert result.candidate.objective_value.total_leftover == 4
    assert result.candidate.objective_value.distinct_pattern_count == 1
    # LIMIT_REACHED never carries a candidate (see
    # docs/resource-limits.md, "Termination and incumbent rules"): a
    # complete, available incumbent is reported as FEASIBLE instead, the
    # same vocabulary solve() already uses for any unproved-but-verified
    # candidate.
    assert result.diagnostics.backend_status is BackendStatus.FEASIBLE
    assert result.diagnostics.termination_reason is TerminationReason.TIME_LIMIT
    assert result.diagnostics.completed_objective_stages == (ObjectiveStage.STOCK_PIECE_COUNT,)
    # The primary stage's proof is not thrown away just because the
    # secondary stage never got to run -- but it also must not imply that
    # the unattempted secondary or tertiary stages were themselves proved.
    assert result.diagnostics.lower_bound == 1
    assert result.diagnostics.upper_bound == 1
    # Exactly one solver call (the primary stage's) was ever attempted.
    assert result.diagnostics.iterations == 1


def test_deadline_exhausted_before_the_tertiary_stage_returns_the_secondary_incumbent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same rule as the primary-to-secondary boundary above, one stage
    later: stages 1 and 2 both reach a proven optimum, stage 3 (distinct
    pattern count) is never attempted because the shared deadline is
    already spent, and the resulting model state -- valid for the whole
    problem, optimal on both proved prefixes -- must still be returned as
    a FEASIBLE incumbent rather than discarded.
    """
    problem = _one_piece_problem()
    patterns = enumerate_patterns_for_problem(problem).patterns
    clock = FakeClock(start=0.0)
    monkeypatch.setattr(backend_module, "perf_counter", clock)
    monkeypatch.setattr(timing_module, "perf_counter", clock)
    # Stages 1 and 2 together consume the entire configured 6-second
    # budget (3 seconds each); stage 3 must never be attempted.
    adapter = ClockAdvancingAdapter(clock, seconds_per_call=3.0)

    result = solve_exact_enumerated_pipeline(
        problem,
        patterns,
        adapter,  # type: ignore[arg-type]
        BackendOptions(time_limit_seconds=6.0),
        deadline=Deadline.from_start(started_at=clock.now, time_limit_seconds=6.0),
    )

    # Stage 3 must never be started: its options.time_limit_seconds would
    # already be <= 0.
    assert adapter.solve_calls == 2
    assert result.candidate is not None
    assert result.candidate.objective_value.stock_piece_count == 1
    assert result.candidate.objective_value.total_leftover == 4
    assert result.candidate.objective_value.distinct_pattern_count == 1
    assert result.diagnostics.backend_status is BackendStatus.FEASIBLE
    assert result.diagnostics.termination_reason is TerminationReason.TIME_LIMIT
    assert result.diagnostics.completed_objective_stages == (
        ObjectiveStage.STOCK_PIECE_COUNT,
        ObjectiveStage.TOTAL_LEFTOVER,
    )
    # Proved only through the secondary stage -- the unattempted tertiary
    # stage's objective must not be implied by these bounds.
    assert result.diagnostics.lower_bound == 1
    assert result.diagnostics.upper_bound == 1
    # Two solver calls (primary, then secondary) were attempted; the
    # tertiary stage's was not.
    assert result.diagnostics.iterations == 2


def test_deadline_already_expired_before_the_pipeline_starts_skips_every_stage() -> None:
    problem = _one_piece_problem()
    patterns = enumerate_patterns_for_problem(problem).patterns
    clock = FakeClock(start=0.0)
    adapter = ClockAdvancingAdapter(clock, seconds_per_call=0.0)

    result = solve_exact_enumerated_pipeline(
        problem,
        patterns,
        adapter,  # type: ignore[arg-type]
        BackendOptions(time_limit_seconds=1.0),
        # Anchored so that, by the time the pipeline checks it, the
        # deadline has already passed.
        deadline=Deadline.from_start(started_at=-10.0, time_limit_seconds=1.0),
    )

    assert adapter.solve_calls == 0
    assert result.candidate is None
    assert result.diagnostics.backend_status is BackendStatus.LIMIT_REACHED
    assert result.diagnostics.termination_reason is TerminationReason.TIME_LIMIT
    assert result.diagnostics.completed_objective_stages == ()
    assert result.diagnostics.lower_bound is None
    assert result.diagnostics.upper_bound is None
    # No solver call was ever attempted: the shared deadline was already
    # spent before the pipeline could start its first stage.
    assert result.diagnostics.iterations == 0


def test_enumeration_time_limit_is_reported_distinctly_from_pattern_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deadline that expires during enumeration must be reported as
    TIME_LIMIT (not PATTERN_LIMIT), and must never reach the solver -- the
    same "discard the partial set" rule pattern_limit already follows (see
    docs/resource-limits.md).
    """
    adapter = FakeAdapter()
    backend = ExactEnumeratedBackend(adapter)  # type: ignore[arg-type]
    problem = _multi_pattern_problem()
    clock = FakeClock(start=1_000.0)
    monkeypatch.setattr(backend_module, "perf_counter", clock)
    monkeypatch.setattr(timing_module, "perf_counter", clock)
    # An explicit, already-expired shared deadline: ExactEnumeratedBackend
    # prefers options.deadline over anchoring a fresh one from
    # time_limit_seconds, exactly as solve() -> backend.solve() does.
    expired_deadline = Deadline.from_start(started_at=0.0, time_limit_seconds=1.0)

    result = backend.solve(
        problem,
        BackendOptions(time_limit_seconds=1.0, deadline=expired_deadline),
    )

    assert adapter.solve_calls == 0
    assert result.candidate is None
    assert result.diagnostics.backend_status is BackendStatus.LIMIT_REACHED
    assert result.diagnostics.termination_reason is TerminationReason.TIME_LIMIT
    assert result.diagnostics.patterns_generated == 0
    assert result.diagnostics.completed_objective_stages == ()
