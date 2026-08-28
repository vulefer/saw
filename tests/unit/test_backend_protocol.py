from dataclasses import FrozenInstanceError

import pytest

from saw.backends.base import (
    BackendAvailability,
    BackendCapabilities,
    BackendOptions,
    BackendSupport,
    OptimizationBackend,
    UnverifiedBackendResult,
    UnverifiedCandidate,
)
from saw.models import (
    BackendStatus,
    Diagnostics,
    ObjectiveStage,
    ObjectiveValue,
    Problem,
    TerminationReason,
)


def make_diagnostics() -> Diagnostics:
    return Diagnostics(
        termination_reason=TerminationReason.COMPLETED,
        backend_status=BackendStatus.OPTIMAL,
        elapsed_time=0.1,
        patterns_generated=0,
        iterations=1,
        completed_objective_stages=tuple(ObjectiveStage),
        lower_bound=0,
        upper_bound=0,
    )


class FakeBackend:
    @property
    def backend_id(self) -> str:
        return "fake"

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            proves_optimality=True,
            proves_infeasibility=True,
            supports_time_limit=True,
            supports_pattern_limit=True,
            objective_stages=frozenset(ObjectiveStage),
        )

    def check_availability(self) -> BackendAvailability:
        return BackendAvailability(available=True, backend_version="1.0")

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
        candidate = UnverifiedCandidate(
            cut_patterns=(),
            objective_value=ObjectiveValue(0, 0, 0),
        )
        return UnverifiedBackendResult(candidate, make_diagnostics())


def test_options_are_immutable() -> None:
    options = BackendOptions(time_limit_seconds=10.0, pattern_limit=100)

    with pytest.raises(FrozenInstanceError):
        options.pattern_limit = 200


def test_capabilities_take_an_immutable_snapshot_of_objective_stages() -> None:
    stages = {ObjectiveStage.STOCK_PIECE_COUNT}

    capabilities = BackendCapabilities(
        proves_optimality=True,
        proves_infeasibility=True,
        supports_time_limit=True,
        supports_pattern_limit=True,
        objective_stages=stages,
    )
    stages.add(ObjectiveStage.TOTAL_LEFTOVER)

    assert capabilities.objective_stages == frozenset(
        {ObjectiveStage.STOCK_PIECE_COUNT}
    )


def test_availability_can_explain_why_a_backend_cannot_run() -> None:
    availability = BackendAvailability(
        available=False,
        reason="Required solver executable was not found.",
    )

    assert availability.available is False
    assert availability.reason == "Required solver executable was not found."
    assert availability.backend_version is None


def test_support_can_explain_why_a_request_cannot_be_handled() -> None:
    support = BackendSupport(
        supported=False,
        reason="The problem exceeds the configured pattern limit.",
    )

    assert support.supported is False
    assert support.reason == "The problem exceeds the configured pattern limit."


def test_empty_candidate_is_distinct_from_no_candidate() -> None:
    candidate = UnverifiedCandidate(
        cut_patterns=(),
        objective_value=ObjectiveValue(0, 0, 0),
    )

    with_candidate = UnverifiedBackendResult(candidate, make_diagnostics())
    without_candidate = UnverifiedBackendResult(None, make_diagnostics())

    assert with_candidate.candidate is candidate
    assert with_candidate.candidate.cut_patterns == ()
    assert without_candidate.candidate is None


def test_backend_protocol_supports_structural_implementations() -> None:
    backend = FakeBackend()
    problem = Problem(stocks=(), demands=())
    options = BackendOptions()

    assert isinstance(backend, OptimizationBackend)
    assert backend.check_availability().available is True
    assert backend.check_support(problem, options).supported is True
    assert backend.solve(problem, options).candidate is not None
