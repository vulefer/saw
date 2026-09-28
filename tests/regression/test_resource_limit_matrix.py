"""Resource-limit regression matrix (Linear V020-23).

Sweeps ``default``, ``disabled_or_overridden``, ``exact_boundary``, and
``exceeded`` cases for every resource limit documented in
docs/resource-limits.md (``problem_size_limit``, ``numeric_magnitude_limit``,
``time_limit_seconds``, ``pattern_limit``) through the public
``saw.solve()`` Python entry point, and enforces the two whole-contract
invariants that must hold no matter which limit fired:

* a limit outcome is never translated into ``OPTIMAL`` or ``INFEASIBLE``
  without proof;
* any incumbent a limit outcome does carry has passed independent
  verification.

**Python entry point only.** Per ``PROJECT_PLAN.md`` and
``docs/cli.md`` (written explicitly "before its implementation"),
``src/saw/cli.py`` -- the "saw console entry point" -- is not implemented in
this checkout yet: none of the CLI tickets (V020-12 through V020-16 in
``outputs/v0.2.0/saw-v0.2.0-linear-import.csv``) are among this ticket's
(V020-23) blockers, and the CLI-inclusive version of this matrix is a
separate, later ticket -- "Verify all documented failure paths through the
CLI" (V020-42) -- which is itself blocked by both V020-16 and this ticket.
There is therefore no CLI behavior to include here yet; this module covers
every row for the Python entry point only.

Individual limits already have dedicated preflight/boundary suites
(``test_solve_size_limits.py``, ``test_solve_time_limit.py``,
``test_solve_pattern_limit.py``) and a dedicated pipeline-level
deadline-sharing suite (``test_exact_backend_strategy.py``, which drives
``solve_exact_enumerated_pipeline`` directly with a fake adapter). This
module adds the matrix view those don't: one place that walks all four
limits through the same default/disabled-or-overridden/exact-boundary/
exceeded shape, plus a real end-to-end mid-solve time-limit case -- through
the public ``solve()`` API, the actual PuLP/CBC backend, and the actual
independent verifier, not stand-ins -- since no existing test drives that
combination all the way through.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module

import pytest

from saw import Demand, Problem, Solution, SolveOptions, Stock, solve
from saw.backends.base import (
    BackendAvailability,
    BackendCapabilities,
    BackendOptions,
    BackendSupport,
    UnverifiedBackendResult,
)
from saw.backends.exact_enumerated import ExactEnumeratedBackend
from saw.backends.pulp_backend import PulpCbcAdapter
from saw.models import (
    BackendStatus,
    Diagnostics,
    ObjectiveStage,
    SolutionStatus,
    TerminationReason,
)

# --- shared fakes ------------------------------------------------------------


class FakeClock:
    """A controllable stand-in for time.perf_counter().

    ``step`` (default 0.0) is added on every call before the reading is
    returned; a step much larger than a test's configured
    ``time_limit_seconds`` guarantees the very next read after a deadline is
    anchored already lands past it, deterministically, regardless of real
    elapsed time (see the "fake-clock trap" note in the other resource-limit
    test modules). ``expire()`` additionally supports flipping the clock
    into "the deadline has already passed" mode at a point this module
    chooses explicitly -- after a real backend call returns -- rather than
    after a fixed number of reads, so it stays correct even if unrelated
    code changes how many times the deadline is checked along the way.
    """

    def __init__(self, start: float = 0.0, step: float = 0.0) -> None:
        self.now = start
        self.step = step
        self._expired = False

    def __call__(self) -> float:
        self.now += self.step
        if self._expired:
            self.now += 1_000.0
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def expire(self) -> None:
        self._expired = True


@dataclass
class RecordingBackend:
    """A minimal stand-in backend that records whether it was ever reached.

    Every preflight-limit outcome must never touch the backend at all, so
    every test that expects one also asserts ``backend.calls == []``,
    mirroring the established pattern in the other resource-limit test
    modules.
    """

    selected_id: str = "recording"
    availability: BackendAvailability = field(
        default_factory=lambda: BackendAvailability(available=True)
    )
    support: BackendSupport = field(default_factory=lambda: BackendSupport(supported=True))
    calls: list[str] = field(default_factory=list)
    received_options: BackendOptions | None = None

    @property
    def backend_id(self) -> str:
        return self.selected_id

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            proves_optimality=True,
            proves_infeasibility=True,
            supports_time_limit=True,
            supports_pattern_limit=True,
            objective_stages=frozenset(ObjectiveStage),
        )

    def check_availability(self) -> BackendAvailability:
        self.calls.append("check_availability")
        return self.availability

    def check_support(
        self,
        problem: Problem,
        options: BackendOptions,
    ) -> BackendSupport:
        self.calls.append("check_support")
        return self.support

    def solve(
        self,
        problem: Problem,
        options: BackendOptions,
    ) -> UnverifiedBackendResult:
        self.calls.append("solve")
        self.received_options = options
        # INFEASIBLE needs no candidate and skips independent verification
        # (see saw.solve.solve), so this stand-in works for arbitrary
        # problem contents without needing a real, verifiable cut plan.
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


def _install_backend(
    monkeypatch: pytest.MonkeyPatch,
    backend: RecordingBackend,
) -> None:
    solve_module = import_module("saw.solve")
    monkeypatch.setattr(solve_module, "_BACKENDS", {"exact_enumerated": backend})


def _patch_clock(monkeypatch: pytest.MonkeyPatch, clock: FakeClock) -> None:
    solve_module = import_module("saw.solve")
    timing_module = import_module("saw.timing")
    monkeypatch.setattr(solve_module, "perf_counter", clock)
    monkeypatch.setattr(timing_module, "perf_counter", clock)


def _stocks(count: int) -> tuple[Stock, ...]:
    return tuple(Stock(f"stock-{index}", "steel", length=10, quantity=1) for index in range(count))


def _demands(count: int) -> tuple[Demand, ...]:
    return tuple(Demand(f"demand-{index}", "steel", length=5, quantity=1) for index in range(count))


def _multi_pattern_problem() -> Problem:
    # Enumerates to exactly 3 non-empty patterns: {piece-4: 1},
    # {piece-6: 1}, and {piece-4: 1, piece-6: 1}. The combined pattern is
    # the unique proven optimum (1 stock piece, 0 leftover).
    return Problem(
        stocks=(Stock("stock-10", "steel", 10, 1),),
        demands=(
            Demand("piece-6", "steel", 6, 1),
            Demand("piece-4", "steel", 4, 1),
        ),
    )


# --- universal invariants (V020-23 acceptance criteria 2 and 3) --------------


def _assert_never_translated_to_a_stronger_mathematical_claim(
    solution: Solution,
) -> None:
    """A limit result is never reported as OPTIMAL or INFEASIBLE."""

    assert solution.solution_status is not SolutionStatus.OPTIMAL
    assert solution.solution_status is not SolutionStatus.INFEASIBLE


def _assert_any_incumbent_is_independently_verified(solution: Solution) -> None:
    """Any incumbent a limit-affected solve() call returns has passed
    saw.verify_solution() -- solve() attaches a verification_report only
    once verification has actually run and succeeded (see saw.solve.solve),
    so its mere presence already proves this; asserting is_valid here keeps
    that connection explicit for this regression module.
    """

    if solution.cut_patterns or solution.objective_value is not None:
        assert solution.verification_report is not None
    if solution.verification_report is not None:
        assert solution.verification_report.is_valid


# ==============================================================================
# problem_size_limit
# ==============================================================================


def test_problem_size_limit_default_accepts_two_hundred_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = RecordingBackend()
    _install_backend(monkeypatch, backend)

    result = solve(Problem(stocks=_stocks(100), demands=_demands(100)))

    assert result.solution_status is SolutionStatus.INFEASIBLE
    assert backend.calls == ["check_availability", "check_support", "solve"]


def test_problem_size_limit_disabled_or_overridden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = RecordingBackend()
    _install_backend(monkeypatch, backend)
    oversized_by_default = Problem(stocks=_stocks(201), demands=())

    disabled = solve(oversized_by_default, SolveOptions(problem_size_limit=None))
    overridden = solve(oversized_by_default, SolveOptions(problem_size_limit=300))

    assert disabled.solution_status is SolutionStatus.INFEASIBLE
    assert overridden.solution_status is SolutionStatus.INFEASIBLE
    assert backend.calls == [
        "check_availability",
        "check_support",
        "solve",
        "check_availability",
        "check_support",
        "solve",
    ]


def test_problem_size_limit_exact_boundary_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = RecordingBackend()
    _install_backend(monkeypatch, backend)
    problem = Problem(stocks=_stocks(150), demands=_demands(50))

    result = solve(problem, SolveOptions(problem_size_limit=200))

    assert result.solution_status is SolutionStatus.INFEASIBLE
    assert backend.calls == ["check_availability", "check_support", "solve"]


def _problem_size_limit_exceeded_solution(
    monkeypatch: pytest.MonkeyPatch,
) -> Solution:
    backend = RecordingBackend()
    _install_backend(monkeypatch, backend)
    problem = Problem(stocks=_stocks(150), demands=_demands(51))

    result = solve(problem, SolveOptions(problem_size_limit=200))

    assert result.solution_status is SolutionStatus.LIMIT
    assert result.diagnostics.termination_reason is TerminationReason.SIZE_LIMIT
    assert backend.calls == []
    return result


def test_problem_size_limit_exceeded_is_rejected_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _problem_size_limit_exceeded_solution(monkeypatch)


# ==============================================================================
# numeric_magnitude_limit
# ==============================================================================


def test_numeric_magnitude_limit_default_accepts_one_million(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = RecordingBackend()
    _install_backend(monkeypatch, backend)
    problem = Problem(
        stocks=(Stock("stock", "steel", length=1_000_000, quantity=1),),
        demands=(Demand("demand", "steel", length=5, quantity=1),),
    )

    result = solve(problem)

    assert result.solution_status is SolutionStatus.INFEASIBLE
    assert backend.calls == ["check_availability", "check_support", "solve"]


def test_numeric_magnitude_limit_disabled_or_overridden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = RecordingBackend()
    _install_backend(monkeypatch, backend)
    oversized_by_default = Problem(
        stocks=(Stock("stock", "steel", length=5_000_000, quantity=1),),
        demands=(Demand("demand", "steel", length=5, quantity=1),),
    )

    disabled = solve(
        oversized_by_default,
        SolveOptions(numeric_magnitude_limit=None),
    )
    overridden = solve(
        oversized_by_default,
        SolveOptions(numeric_magnitude_limit=10_000_000),
    )

    assert disabled.solution_status is SolutionStatus.INFEASIBLE
    assert overridden.solution_status is SolutionStatus.INFEASIBLE


def test_numeric_magnitude_limit_exact_boundary_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = RecordingBackend()
    _install_backend(monkeypatch, backend)
    problem = Problem(
        stocks=(Stock("stock", "steel", length=1_000, quantity=1),),
        demands=(Demand("demand", "steel", length=5, quantity=1),),
    )

    result = solve(problem, SolveOptions(numeric_magnitude_limit=1_000))

    assert result.solution_status is SolutionStatus.INFEASIBLE
    assert backend.calls == ["check_availability", "check_support", "solve"]


def _numeric_magnitude_limit_exceeded_solution(
    monkeypatch: pytest.MonkeyPatch,
) -> Solution:
    backend = RecordingBackend()
    _install_backend(monkeypatch, backend)
    problem = Problem(
        stocks=(Stock("stock", "steel", length=1_001, quantity=1),),
        demands=(Demand("demand", "steel", length=5, quantity=1),),
    )

    result = solve(problem, SolveOptions(numeric_magnitude_limit=1_000))

    assert result.solution_status is SolutionStatus.LIMIT
    assert result.diagnostics.termination_reason is TerminationReason.SIZE_LIMIT
    assert backend.calls == []
    return result


def test_numeric_magnitude_limit_exceeded_is_rejected_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _numeric_magnitude_limit_exceeded_solution(monkeypatch)


# ==============================================================================
# time_limit_seconds
# ==============================================================================


def test_time_limit_seconds_and_pattern_limit_default_to_the_documented_budgets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """docs/resource-limits.md: the v0.2.0 default is 30.0 seconds and
    50,000 patterns -- omitting SolveOptions entirely must still apply both,
    not reach the backend unlimited. This is the ``pattern_limit`` matrix
    row's default case as well as ``time_limit_seconds``'s: both defaults
    are only observable together, as the BackendOptions actually handed to
    the backend.
    """
    backend = RecordingBackend()
    _install_backend(monkeypatch, backend)

    solve(Problem(stocks=(), demands=()))

    assert backend.received_options == BackendOptions(
        time_limit_seconds=30.0,
        pattern_limit=50_000,
    )


def test_time_limit_seconds_disabled_or_overridden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock(start=0.0)
    _patch_clock(monkeypatch, clock)
    backend = RecordingBackend()
    _install_backend(monkeypatch, backend)
    problem = Problem(
        stocks=(Stock("stock", "steel", 10, 1),),
        demands=(Demand("demand", "steel", 5, 1),),
    )
    clock.advance(1_000_000.0)  # an enormous amount of "real" time passing

    disabled = solve(problem, SolveOptions(time_limit_seconds=None))
    overridden = solve(problem, SolveOptions(time_limit_seconds=5.0))

    assert disabled.solution_status is SolutionStatus.INFEASIBLE
    assert disabled.diagnostics.termination_reason is TerminationReason.COMPLETED
    assert overridden.solution_status is SolutionStatus.INFEASIBLE
    assert overridden.diagnostics.termination_reason is TerminationReason.COMPLETED


def test_time_limit_seconds_exact_boundary_reaches_the_backend_with_full_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock(start=0.0)
    _patch_clock(monkeypatch, clock)
    backend = RecordingBackend()
    _install_backend(monkeypatch, backend)
    problem = Problem(
        stocks=(Stock("stock", "steel", 10, 1),),
        demands=(Demand("demand", "steel", 5, 1),),
    )

    result = solve(problem, SolveOptions(time_limit_seconds=30.0))

    assert result.solution_status is SolutionStatus.INFEASIBLE
    assert backend.calls == ["check_availability", "check_support", "solve"]
    assert backend.received_options is not None
    assert backend.received_options.deadline is not None
    assert backend.received_options.deadline.remaining_seconds() == pytest.approx(30.0)


def _time_limit_seconds_exceeded_solution(
    monkeypatch: pytest.MonkeyPatch,
) -> Solution:
    # A step much larger than time_limit_seconds guarantees that the very
    # next perf_counter() read after started_at (solve()'s own deadline
    # check) already lands past the deadline, deterministically.
    clock = FakeClock(start=0.0, step=1_000.0)
    _patch_clock(monkeypatch, clock)
    backend = RecordingBackend(
        availability=BackendAvailability(available=False, reason="should never be consulted")
    )
    _install_backend(monkeypatch, backend)
    problem = Problem(
        stocks=(Stock("stock", "steel", 10, 1),),
        demands=(Demand("demand", "steel", 5, 1),),
    )

    result = solve(problem, SolveOptions(time_limit_seconds=1.0))

    assert result.solution_status is SolutionStatus.LIMIT
    assert result.diagnostics.termination_reason is TerminationReason.TIME_LIMIT
    assert backend.calls == []
    return result


def test_time_limit_seconds_exceeded_short_circuits_before_the_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _time_limit_seconds_exceeded_solution(monkeypatch)


# --- real end-to-end: mid-solve deadline exhaustion --------------------------


class _ExpireClockAfterFirstSolve:
    """Wraps the real ``PulpCbcAdapter``; the moment its first ``solve()``
    call returns, this flips the shared ``FakeClock`` into "the deadline has
    already passed" for every later read.

    This drives a genuine multi-stage lexicographic solve through the real
    CBC backend -- stage 1 (stock-piece count) actually solves the model and
    proves its optimum -- and stops the shared deadline exactly at the
    boundary between stage 1 and stage 2, without guessing how many
    ``perf_counter()`` reads happen along the way.
    """

    def __init__(self, adapter: PulpCbcAdapter, clock: FakeClock) -> None:
        self._adapter = adapter
        self._clock = clock

    def check_availability(self) -> BackendAvailability:
        return self._adapter.check_availability()

    def solve(self, model, options):
        status = self._adapter.solve(model, options)
        self._clock.expire()
        return status


def test_real_mid_solve_time_limit_returns_an_independently_verified_incumbent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The genuinely-new coverage this matrix adds: an actual mid-solve
    deadline exhaustion, through the public solve() API, the real PuLP/CBC
    backend, and the real independent verifier -- not a fake adapter or a
    stand-in backend.

    Per docs/resource-limits.md, "Earlier-stage incumbents remain eligible
    after a later stage reaches the time limit": stage 1 (stock-piece count)
    solves this problem for real and proves its optimum; stage 2 never
    starts because the shared deadline is already spent by the time it
    would; the resulting incumbent -- a real CBC solution, not a synthetic
    one -- must still pass real verification and be returned as FEASIBLE
    with termination_reason TIME_LIMIT (never LIMIT: LIMIT_REACHED never
    carries a candidate, per the exact_enumerated backend's own contract).
    """
    clock = FakeClock(start=0.0)
    _patch_clock(monkeypatch, clock)
    solve_module = import_module("saw.solve")
    real_adapter = PulpCbcAdapter()
    wrapped_backend = ExactEnumeratedBackend(
        _ExpireClockAfterFirstSolve(real_adapter, clock)  # type: ignore[arg-type]
    )
    monkeypatch.setattr(solve_module, "_BACKENDS", {"exact_enumerated": wrapped_backend})
    problem = Problem(
        stocks=(Stock("stock-10", "steel", 10, 1),),
        demands=(Demand("piece-6", "steel", 6, 1),),
    )

    result = solve(problem, SolveOptions(time_limit_seconds=5.0))

    assert result.solution_status is SolutionStatus.FEASIBLE
    assert result.diagnostics.termination_reason is TerminationReason.TIME_LIMIT
    assert result.diagnostics.completed_objective_stages == (ObjectiveStage.STOCK_PIECE_COUNT,)
    assert result.diagnostics.backend_id == "exact_enumerated"
    assert result.diagnostics.backend_version is not None
    assert result.objective_value is not None
    assert result.objective_value.stock_piece_count == 1
    assert result.objective_value.total_leftover == 4
    assert len(result.cut_patterns) == 1
    assert result.cut_patterns[0].piece_counts == {"piece-6": 1}
    # The load-bearing assertion: a real backend-produced, mid-deadline
    # incumbent still passed independent verification before being
    # returned.
    assert result.verification_report is not None
    assert result.verification_report.is_valid


# ==============================================================================
# pattern_limit
# ==============================================================================
#
# The default case is exercised by
# test_time_limit_seconds_and_pattern_limit_default_to_the_documented_budgets
# above: pattern_limit's default is only observable together with
# time_limit_seconds's, as the one BackendOptions actually handed to the
# backend.


def test_pattern_limit_disabled_or_overridden() -> None:
    problem = _multi_pattern_problem()

    disabled = solve(problem, SolveOptions(pattern_limit=None))
    overridden = solve(problem, SolveOptions(pattern_limit=1_000))

    assert disabled.solution_status is SolutionStatus.OPTIMAL
    assert disabled.diagnostics.patterns_generated == 3
    assert overridden.solution_status is SolutionStatus.OPTIMAL
    assert overridden.diagnostics.patterns_generated == 3


def test_pattern_limit_exact_boundary_still_solves_to_optimality() -> None:
    result = solve(_multi_pattern_problem(), SolveOptions(pattern_limit=3))

    assert result.solution_status is SolutionStatus.OPTIMAL
    assert result.diagnostics.termination_reason is TerminationReason.COMPLETED
    assert result.diagnostics.patterns_generated == 3
    assert result.verification_report is not None
    assert result.verification_report.is_valid


def _pattern_limit_exceeded_solution() -> Solution:
    # The problem enumerates to 3 patterns; a limit of 2 is hit attempting
    # the 3rd.
    result = solve(_multi_pattern_problem(), SolveOptions(pattern_limit=2))

    assert result.solution_status is SolutionStatus.LIMIT
    assert result.diagnostics.termination_reason is TerminationReason.PATTERN_LIMIT
    assert result.diagnostics.patterns_generated == 2
    return result


def test_pattern_limit_exceeded_discards_the_partial_enumeration() -> None:
    _pattern_limit_exceeded_solution()


# ==============================================================================
# cross-cutting: the two universal invariants, across every exceeded case
# ==============================================================================


@pytest.mark.parametrize(
    "produce_exceeded_solution",
    [
        _problem_size_limit_exceeded_solution,
        _numeric_magnitude_limit_exceeded_solution,
        _time_limit_seconds_exceeded_solution,
        lambda monkeypatch: _pattern_limit_exceeded_solution(),
    ],
    ids=[
        "problem_size_limit",
        "numeric_magnitude_limit",
        "time_limit_seconds",
        "pattern_limit",
    ],
)
def test_every_exceeded_limit_never_claims_optimality_or_infeasibility(
    monkeypatch: pytest.MonkeyPatch,
    produce_exceeded_solution,
) -> None:
    solution = produce_exceeded_solution(monkeypatch)

    assert solution.solution_status is SolutionStatus.LIMIT
    _assert_never_translated_to_a_stronger_mathematical_claim(solution)
    _assert_any_incumbent_is_independently_verified(solution)
