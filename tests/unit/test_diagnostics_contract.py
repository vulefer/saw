"""Diagnostics-contract tests.

These tests verify, for every public ``SolutionStatus``, that
``Solution.diagnostics`` matches the rules documented in
``docs/formats.md`` ("Diagnostics" and "Diagnostics by solution_status")
and enforced structurally by ``src/saw/schemas/solution-v1.schema.json``. They also
cover the two Python-only fields, ``backend_id`` and ``backend_version``
(see ``docs/formats.md``, "Python-only diagnostics fields"): populated as
soon as ``solve()`` reaches a backend's ``check_availability()``, and
``None`` for every outcome decided before that point.

Where a status is reachable through the real ``exact_enumerated`` backend
without contrivance (``optimal``, ``infeasible``, and the preflight
``limit`` outcomes), these tests exercise it end to end. ``feasible``,
``limit`` with a verified incumbent, ``unsupported``, and ``error`` are
exercised through a recording backend double, mirroring the established
pattern in ``tests/unit/test_solve.py`` and
``tests/unit/test_solve_size_limits.py`` -- ``unsupported`` and ``error``
are not currently reachable through the bundled backend at all.
"""

from __future__ import annotations

import math
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
    UnverifiedCandidate,
)
from saw.models import (
    BackendStatus,
    CutPattern,
    Diagnostics,
    ObjectiveStage,
    ObjectiveValue,
    Solution,
    SolutionStatus,
    TerminationReason,
)
from saw.patterns import Pattern


# --- shared assertion helpers -----------------------------------------------


_VALID_STAGE_PREFIXES = (
    (),
    (ObjectiveStage.STOCK_PIECE_COUNT,),
    (ObjectiveStage.STOCK_PIECE_COUNT, ObjectiveStage.TOTAL_LEFTOVER),
    (
        ObjectiveStage.STOCK_PIECE_COUNT,
        ObjectiveStage.TOTAL_LEFTOVER,
        ObjectiveStage.DISTINCT_PATTERN_COUNT,
    ),
)


def _assert_general_diagnostics_invariants(diagnostics: Diagnostics) -> None:
    """Rules that hold for every Diagnostics object, whatever the status."""

    assert math.isfinite(diagnostics.elapsed_time)
    assert diagnostics.elapsed_time >= 0.0
    assert diagnostics.patterns_generated >= 0
    assert diagnostics.iterations >= 0
    assert diagnostics.completed_objective_stages in _VALID_STAGE_PREFIXES
    if diagnostics.lower_bound is not None:
        assert diagnostics.lower_bound >= 0
    if diagnostics.upper_bound is not None:
        assert diagnostics.upper_bound >= 0
    if diagnostics.lower_bound is not None and diagnostics.upper_bound is not None:
        assert diagnostics.lower_bound <= diagnostics.upper_bound


def _assert_diagnostics_contract(solution: Solution) -> None:
    """Verify diagnostics match the closed status/diagnostic combinations
    documented in docs/formats.md, "Diagnostics by solution_status".
    """

    diagnostics = solution.diagnostics
    _assert_general_diagnostics_invariants(diagnostics)
    status = solution.solution_status

    if status is SolutionStatus.OPTIMAL:
        assert diagnostics.termination_reason is TerminationReason.COMPLETED
        assert diagnostics.backend_status is BackendStatus.OPTIMAL
        assert diagnostics.completed_objective_stages == tuple(ObjectiveStage)
        assert diagnostics.lower_bound is not None
        assert diagnostics.upper_bound is not None
        assert diagnostics.lower_bound == diagnostics.upper_bound
        assert solution.verification_report is not None
        assert solution.verification_report.is_valid
    elif status is SolutionStatus.FEASIBLE:
        assert diagnostics.backend_status is BackendStatus.FEASIBLE
        assert len(diagnostics.completed_objective_stages) < 3
        assert solution.verification_report is not None
        assert solution.verification_report.is_valid
    elif status is SolutionStatus.INFEASIBLE:
        assert diagnostics.termination_reason is TerminationReason.COMPLETED
        assert diagnostics.backend_status is BackendStatus.INFEASIBLE
        assert diagnostics.completed_objective_stages == ()
        assert diagnostics.lower_bound is None
        assert diagnostics.upper_bound is None
        assert solution.cut_patterns == ()
        assert solution.objective_value is None
        assert solution.verification_report is None
    elif status is SolutionStatus.LIMIT:
        assert diagnostics.backend_status is BackendStatus.LIMIT_REACHED
        assert len(diagnostics.completed_objective_stages) < 3
        if solution.verification_report is None:
            # "limit" without a verified incumbent: the whole verified-plan
            # group is absent, per docs/formats.md.
            assert solution.cut_patterns == ()
            assert solution.objective_value is None
            assert diagnostics.upper_bound is None
        else:
            assert solution.verification_report.is_valid
    elif status is SolutionStatus.UNSUPPORTED:
        assert diagnostics.termination_reason is (
            TerminationReason.UNSUPPORTED_PROBLEM
        )
        assert diagnostics.backend_status is BackendStatus.NOT_RUN
        assert diagnostics.completed_objective_stages == ()
        assert diagnostics.lower_bound is None
        assert diagnostics.upper_bound is None
        assert solution.cut_patterns == ()
        assert solution.objective_value is None
        assert solution.verification_report is None
    elif status is SolutionStatus.ERROR:
        assert diagnostics.completed_objective_stages == ()
        assert diagnostics.lower_bound is None
        assert diagnostics.upper_bound is None
        assert solution.cut_patterns == ()
        assert solution.objective_value is None
        assert solution.verification_report is None
    else:
        pytest.fail(f"unhandled solution_status {status!r}")


# --- a recording backend double for the statuses the bundled backend never
#     produces on its own (unsupported, error) or only produces awkwardly
#     (feasible, limit with a verified incumbent) ---------------------------


@dataclass
class RecordingBackend:
    result: UnverifiedBackendResult
    selected_id: str = "recording"
    availability: BackendAvailability = field(
        default_factory=lambda: BackendAvailability(
            available=True, backend_version="recording-1.0"
        )
    )
    support: BackendSupport = field(
        default_factory=lambda: BackendSupport(supported=True)
    )

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
        return self.availability

    def check_support(
        self,
        problem: Problem,
        options: BackendOptions,
    ) -> BackendSupport:
        return self.support

    def solve(
        self,
        problem: Problem,
        options: BackendOptions,
    ) -> UnverifiedBackendResult:
        return self.result


def _install_backend(
    monkeypatch: pytest.MonkeyPatch,
    backend: RecordingBackend,
) -> None:
    solve_module = import_module("saw.solve")
    monkeypatch.setattr(solve_module, "_BACKENDS", {"exact_enumerated": backend})


def _verifiable_problem_and_candidate() -> tuple[
    Problem, tuple[CutPattern, ...], ObjectiveValue
]:
    """A problem with exactly one feasible non-empty pattern that consumes a
    stock record completely -- so the corresponding candidate is trivially,
    deterministically verifiable regardless of which backend_status it is
    attached to.
    """

    problem = Problem(
        stocks=(Stock("s", "steel", 10, 1),),
        demands=(Demand("d", "steel", 10, 1),),
    )
    cut_patterns = (
        CutPattern(pattern=Pattern("s", {"d": 1}), repetitions=1),
    )
    objective_value = ObjectiveValue(
        stock_piece_count=1, total_leftover=0, distinct_pattern_count=1
    )
    return problem, cut_patterns, objective_value


# --- optimal / infeasible: exercised through the real backend --------------


def test_optimal_diagnostics_contract() -> None:
    problem = Problem(
        stocks=(Stock("stock-10", "steel", 10, 1),),
        demands=(
            Demand("piece-6", "steel", 6, 1),
            Demand("piece-4", "steel", 4, 1),
        ),
    )

    result = solve(problem)

    assert result.solution_status is SolutionStatus.OPTIMAL
    _assert_diagnostics_contract(result)
    # Reached the real backend: both Python-only fields are populated.
    assert result.diagnostics.backend_id == "exact_enumerated"
    assert result.diagnostics.backend_version is not None
    # All three lexicographic stages were attempted to prove optimality.
    assert result.diagnostics.iterations == 3
    assert result.diagnostics.patterns_generated == 3


def test_infeasible_diagnostics_contract() -> None:
    problem = Problem(
        stocks=(Stock("stock-3", "steel", 3, 1),),
        demands=(Demand("piece-4", "steel", 4, 1),),
    )

    result = solve(problem)

    assert result.solution_status is SolutionStatus.INFEASIBLE
    _assert_diagnostics_contract(result)
    # check_availability() and check_support() both ran before enumeration
    # proved infeasibility, so the backend is already known.
    assert result.diagnostics.backend_id == "exact_enumerated"
    assert result.diagnostics.backend_version is not None
    assert result.diagnostics.iterations == 0


# --- limit without an incumbent: exercised through the real preflight guard


def test_limit_without_incumbent_diagnostics_contract() -> None:
    problem = Problem(
        stocks=tuple(
            Stock(f"stock-{index}", "steel", 10, 1) for index in range(201)
        ),
        demands=(),
    )

    result = solve(problem)

    assert result.solution_status is SolutionStatus.LIMIT
    _assert_diagnostics_contract(result)
    # A preflight size guard never reaches a backend at all.
    assert result.diagnostics.backend_id is None
    assert result.diagnostics.backend_version is None
    assert result.diagnostics.iterations == 0


# --- feasible / limit-with-incumbent / unsupported / error: recording double


def test_feasible_diagnostics_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    problem, cut_patterns, objective_value = _verifiable_problem_and_candidate()
    backend = RecordingBackend(
        UnverifiedBackendResult(
            candidate=UnverifiedCandidate(cut_patterns, objective_value),
            diagnostics=Diagnostics(
                termination_reason=TerminationReason.TIME_LIMIT,
                backend_status=BackendStatus.FEASIBLE,
                elapsed_time=1.0,
                patterns_generated=1,
                iterations=1,
                completed_objective_stages=(),
                lower_bound=None,
                upper_bound=1,
            ),
        )
    )
    _install_backend(monkeypatch, backend)

    result = solve(problem)

    assert result.solution_status is SolutionStatus.FEASIBLE
    _assert_diagnostics_contract(result)
    assert result.diagnostics.backend_id == "recording"
    assert result.diagnostics.backend_version == "recording-1.0"


def test_limit_with_incumbent_diagnostics_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    problem, cut_patterns, objective_value = _verifiable_problem_and_candidate()
    backend = RecordingBackend(
        UnverifiedBackendResult(
            candidate=UnverifiedCandidate(cut_patterns, objective_value),
            diagnostics=Diagnostics(
                termination_reason=TerminationReason.TIME_LIMIT,
                backend_status=BackendStatus.LIMIT_REACHED,
                elapsed_time=1.0,
                patterns_generated=1,
                iterations=1,
                completed_objective_stages=(ObjectiveStage.STOCK_PIECE_COUNT,),
                lower_bound=1,
                upper_bound=1,
            ),
        )
    )
    _install_backend(monkeypatch, backend)

    result = solve(problem)

    assert result.solution_status is SolutionStatus.LIMIT
    assert result.verification_report is not None
    _assert_diagnostics_contract(result)
    assert result.diagnostics.backend_id == "recording"
    assert result.diagnostics.backend_version == "recording-1.0"


def test_unsupported_diagnostics_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    problem = Problem(stocks=(), demands=())
    backend = RecordingBackend(
        UnverifiedBackendResult(
            candidate=None,
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
        ),
        support=BackendSupport(supported=False, reason="not supported here"),
    )
    _install_backend(monkeypatch, backend)

    result = solve(problem)

    assert result.solution_status is SolutionStatus.UNSUPPORTED
    _assert_diagnostics_contract(result)
    # check_availability() already ran before check_support() declined the
    # problem, so the backend that would have run it is already known.
    assert result.diagnostics.backend_id == "recording"
    assert result.diagnostics.backend_version == "recording-1.0"


def test_error_diagnostics_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    problem = Problem(stocks=(), demands=())
    backend = RecordingBackend(
        UnverifiedBackendResult(
            candidate=None,
            diagnostics=Diagnostics(
                termination_reason=TerminationReason.BACKEND_FAILURE,
                backend_status=BackendStatus.FAILED,
                elapsed_time=0.5,
                patterns_generated=0,
                iterations=1,
                completed_objective_stages=(),
                lower_bound=None,
                upper_bound=None,
            ),
        )
    )
    _install_backend(monkeypatch, backend)

    result = solve(problem)

    assert result.solution_status is SolutionStatus.ERROR
    _assert_diagnostics_contract(result)
    assert result.diagnostics.backend_id == "recording"
    assert result.diagnostics.backend_version == "recording-1.0"


# --- backend_id/backend_version defaults ------------------------------------


def test_diagnostics_backend_fields_default_to_none() -> None:
    diagnostics = Diagnostics(
        termination_reason=TerminationReason.COMPLETED,
        backend_status=BackendStatus.OPTIMAL,
        elapsed_time=0.0,
        patterns_generated=0,
        iterations=0,
        completed_objective_stages=tuple(ObjectiveStage),
        lower_bound=0,
        upper_bound=0,
    )

    assert diagnostics.backend_id is None
    assert diagnostics.backend_version is None
