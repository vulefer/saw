from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from saw.models import (
    BackendStatus,
    CutPattern,
    Diagnostics,
    ObjectiveStage,
    ObjectiveValue,
    Problem,
    SolutionStatus,
)
from saw.timing import Deadline


# resource configuration for the backend
@dataclass(frozen=True, slots=True)
class BackendOptions:
    time_limit_seconds: float | None = None
    pattern_limit: int | None = None
    # The shared end-to-end deadline for the current solve() call (see
    # docs/resource-limits.md, "One end-to-end time budget"). solve()
    # anchors this once, before problem validation, and threads it down so
    # the exact_enumerated backend can recompute the remaining time before
    # pattern enumeration and before each lexicographic stage instead of
    # reusing time_limit_seconds -- the original configured value -- for
    # every one of them. compare=False excludes it from equality/hash: two
    # BackendOptions built from the same configuration are still equal even
    # though a live Deadline carries a different absolute instant each time
    # solve() runs. A backend that receives options.deadline is None (for
    # example a direct unit-test call that never went through solve())
    # falls back to anchoring its own deadline from time_limit_seconds.
    deadline: Deadline | None = field(default=None, compare=False)


# describes the guarantees and controls a backend provides in general, this will be of more use
# when we add a heuristic approach that cannot prove optimality, only feasibility
@dataclass(frozen=True, slots=True)
class BackendCapabilities:
    proves_optimality: bool
    proves_infeasibility: bool
    supports_time_limit: bool
    supports_pattern_limit: bool
    objective_stages: frozenset[ObjectiveStage]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "objective_stages",
            frozenset(self.objective_stages),
        )


# checks if the backend can run in this environment
@dataclass(frozen=True, slots=True)
class BackendAvailability:
    available: bool
    reason: str | None = None
    backend_version: str | None = None


# this dataclass is useful when one of the config limits is broken, for example the time limit
# is breached
@dataclass(frozen=True, slots=True)
class BackendSupport:
    supported: bool
    reason: str | None = None


# holds a possible solution claimed by a backend before independent verification.
@dataclass(frozen=True, slots=True)
class UnverifiedCandidate:
    cut_patterns: tuple[CutPattern, ...]
    objective_value: ObjectiveValue

    def __post_init__(self) -> None:
        object.__setattr__(self, "cut_patterns", tuple(self.cut_patterns))


# wrapper for backend's untrusted candidate and structured termination diagnostics.
@dataclass(frozen=True, slots=True)
class UnverifiedBackendResult:
    candidate: UnverifiedCandidate | None
    diagnostics: Diagnostics


def translate_backend_status(
    backend_status: BackendStatus,
) -> SolutionStatus:
    """Translate normalized backend outcomes into public solution statuses."""

    return {
        BackendStatus.OPTIMAL: SolutionStatus.OPTIMAL,
        BackendStatus.FEASIBLE: SolutionStatus.FEASIBLE,
        BackendStatus.INFEASIBLE: SolutionStatus.INFEASIBLE,
        BackendStatus.LIMIT_REACHED: SolutionStatus.LIMIT,
        BackendStatus.NOT_RUN: SolutionStatus.ERROR,
        BackendStatus.UNBOUNDED: SolutionStatus.ERROR,
        BackendStatus.NOT_SOLVED: SolutionStatus.ERROR,
        BackendStatus.FAILED: SolutionStatus.ERROR,
        BackendStatus.UNKNOWN: SolutionStatus.ERROR,
    }.get(backend_status, SolutionStatus.ERROR)


# interface for backend classes (strategy pattern)
@runtime_checkable
class OptimizationBackend(Protocol):
    @property
    def backend_id(self) -> str:
        ...

    def capabilities(self) -> BackendCapabilities:
        ...

    def check_availability(self) -> BackendAvailability:
        ...

    def check_support(
        self,
        problem: Problem,
        options: BackendOptions,
    ) -> BackendSupport:
        ...

    def solve(
        self,
        problem: Problem,
        options: BackendOptions,
    ) -> UnverifiedBackendResult:
        ...
