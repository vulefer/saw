from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module

import pytest

from saw import Demand, Problem, SolveOptions, Stock, solve
from saw.backends.base import (
    BackendAvailability,
    BackendCapabilities,
    BackendOptions,
    BackendSupport,
    UnverifiedBackendResult,
)
from saw.models import (
    BackendStatus,
    Diagnostics,
    ObjectiveStage,
    SolutionStatus,
    TerminationReason,
)


class FakeClock:
    """A controllable stand-in for time.perf_counter().

    Deadline tests monkeypatch both saw.solve's and saw.timing's
    perf_counter with the SAME instance, so timing outcomes are exact and
    deterministic instead of depending on real wall-clock speed (see
    docs/resource-limits.md, "One end-to-end time budget").

    ``step`` (default 0.0) is added on every call before the reading is
    returned. Between solve()'s ``started_at = perf_counter()`` read and
    its very next read of the deadline's remaining time, no other code
    calls perf_counter() -- so a ``step`` many times larger than the
    configured ``time_limit_seconds`` guarantees the deadline has already
    passed by that next read, deterministically, regardless of real
    elapsed time. ``advance`` additionally supports moving the clock by an
    exact amount at a point a test chooses explicitly.
    """

    def __init__(self, start: float = 0.0, step: float = 0.0) -> None:
        self.now = start
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class RecordingBackend:
    """A minimal stand-in backend that records whether it was ever reached.

    A preflight time-limit outcome must never touch the backend at all, so
    every test that expects a LIMIT result also asserts ``backend.calls ==
    []`` -- mirroring tests/unit/test_solve_size_limits.py.
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


def _stock() -> Stock:
    return Stock("stock", "steel", length=10, quantity=1)


def _demand() -> Demand:
    return Demand("demand", "steel", length=5, quantity=1)


def _assert_time_limit_result(result) -> None:
    assert result.solution_status is SolutionStatus.LIMIT
    assert result.diagnostics.termination_reason is TerminationReason.TIME_LIMIT
    # backend_status is LIMIT_REACHED -- not NOT_RUN -- even for this
    # preflight-only outcome: the version 1 JSON schema's limitDiagnostics
    # requires backend_status: "limit_reached" for every `limit`
    # solution_status (see src/saw/schemas/solution-v1.schema.json and
    # docs/formats.md, "Diagnostics"). NOT_RUN is reserved for
    # `unsupported`.
    assert result.diagnostics.backend_status is BackendStatus.LIMIT_REACHED
    assert result.diagnostics.patterns_generated == 0
    assert result.diagnostics.iterations == 0
    assert result.diagnostics.completed_objective_stages == ()
    assert result.diagnostics.lower_bound is None
    assert result.diagnostics.upper_bound is None
    # Never reached a backend, so neither Python-only field is populated.
    assert result.diagnostics.backend_id is None
    assert result.diagnostics.backend_version is None
    assert result.cut_patterns == ()
    assert result.objective_value is None
    assert result.verification_report is None
    assert result.diagnostics.elapsed_time >= 0.0


# --- preflight short-circuit ------------------------------------------------


def test_expired_deadline_short_circuits_before_backend_availability_is_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A step much larger than time_limit_seconds guarantees that the very
    # next perf_counter() read after started_at (solve()'s own deadline
    # check) already lands past the deadline, deterministically.
    clock = FakeClock(start=0.0, step=1_000.0)
    _patch_clock(monkeypatch, clock)
    backend = RecordingBackend(
        availability=BackendAvailability(available=False, reason="should never be consulted")
    )
    _install_backend(monkeypatch, backend)
    problem = Problem(stocks=(_stock(),), demands=(_demand(),))

    result = solve(problem, SolveOptions(time_limit_seconds=1.0))

    _assert_time_limit_result(result)
    assert backend.calls == []


def test_healthy_deadline_reaches_the_backend_with_a_live_deadline_attached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock(start=0.0)
    _patch_clock(monkeypatch, clock)
    backend = RecordingBackend()
    _install_backend(monkeypatch, backend)
    problem = Problem(stocks=(_stock(),), demands=(_demand(),))

    result = solve(problem, SolveOptions(time_limit_seconds=30.0))

    assert result.solution_status is SolutionStatus.INFEASIBLE
    assert backend.calls == ["check_availability", "check_support", "solve"]
    assert backend.received_options is not None
    assert backend.received_options.time_limit_seconds == 30.0
    assert backend.received_options.deadline is not None
    assert backend.received_options.deadline.remaining_seconds() == pytest.approx(30.0)


def test_unlimited_time_never_produces_a_preflight_time_limit_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock(start=0.0)
    _patch_clock(monkeypatch, clock)
    backend = RecordingBackend()
    _install_backend(monkeypatch, backend)
    problem = Problem(stocks=(_stock(),), demands=(_demand(),))

    clock.advance(1_000_000.0)  # an enormous amount of "real" time passing

    result = solve(problem, SolveOptions(time_limit_seconds=None))

    assert result.solution_status is SolutionStatus.INFEASIBLE
    assert backend.calls == ["check_availability", "check_support", "solve"]
    assert backend.received_options.deadline is not None
    assert backend.received_options.deadline.remaining_seconds() is None


# --- ordering ---------------------------------------------------------------


def test_size_limit_is_checked_before_the_time_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """docs/resource-limits.md orders the preflight checks as
    problem_size_limit, then numeric_magnitude_limit, then the remaining
    end-to-end time. A problem that trips the size guard must be reported
    as SIZE_LIMIT even when the configured deadline has also already
    expired.
    """
    # A large step guarantees the 1-second deadline is already expired by
    # the time solve() would check it, so this test can actually
    # discriminate "size checked first" from "time checked first" -- if
    # the implementation ever checked time before size, this would observe
    # TIME_LIMIT instead.
    clock = FakeClock(start=0.0, step=1_000.0)
    _patch_clock(monkeypatch, clock)
    backend = RecordingBackend()
    _install_backend(monkeypatch, backend)
    problem = Problem(
        stocks=tuple(Stock(f"stock-{i}", "steel", 10, 1) for i in range(201)),
        demands=(),
    )

    result = solve(problem, SolveOptions(time_limit_seconds=1.0))

    assert result.diagnostics.termination_reason is TerminationReason.SIZE_LIMIT
    assert backend.calls == []


def test_time_limit_is_checked_after_size_guards_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock(start=0.0, step=1_000.0)
    _patch_clock(monkeypatch, clock)
    backend = RecordingBackend()
    _install_backend(monkeypatch, backend)
    problem = Problem(stocks=(_stock(),), demands=(_demand(),))

    result = solve(
        problem,
        SolveOptions(time_limit_seconds=1.0, problem_size_limit=200),
    )

    _assert_time_limit_result(result)
    assert backend.calls == []


def test_time_limit_never_claims_infeasibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock(start=0.0, step=1_000.0)
    _patch_clock(monkeypatch, clock)
    backend = RecordingBackend()
    _install_backend(monkeypatch, backend)
    problem = Problem(stocks=(_stock(),), demands=(_demand(),))

    result = solve(problem, SolveOptions(time_limit_seconds=1.0))

    assert result.solution_status is not SolutionStatus.INFEASIBLE
    assert result.solution_status is SolutionStatus.LIMIT
