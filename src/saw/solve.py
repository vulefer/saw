from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from saw.backends.base import (
    BackendOptions,
    OptimizationBackend,
    UnverifiedBackendResult,
    translate_backend_status,
)
from saw.backends.exact_enumerated.backend import ExactEnumeratedBackend
from saw.exceptions import BackendUnavailableError
from saw.models import (
    BackendStatus,
    Diagnostics,
    Problem,
    Solution,
    SolutionStatus,
    TerminationReason,
)
from saw.validation import validate_problem


@dataclass(frozen=True, slots=True)
class SolveOptions:
    # backend selection wrapper

    backend: str = "exact_enumerated"
    time_limit_seconds: float | None = None
    pattern_limit: int | None = None


_BACKENDS: dict[str, OptimizationBackend] = {
    "exact_enumerated": ExactEnumeratedBackend(),
}


def _select_backend(
    backend_id: str,
    backends: Mapping[str, OptimizationBackend],
) -> OptimizationBackend:
    try:
        return backends[backend_id]
    except KeyError:
        available_ids = ", ".join(sorted(backends))
        raise ValueError(
            f"Unknown backend {backend_id!r}. "
            f"Available backends: {available_ids}."
        ) from None


def _to_backend_options(options: SolveOptions) -> BackendOptions:
    return BackendOptions(
        time_limit_seconds=options.time_limit_seconds,
        pattern_limit=options.pattern_limit,
    )


def _to_public_solution(
    result: UnverifiedBackendResult,
) -> Solution:
    candidate = result.candidate

    return Solution(
        solution_status=translate_backend_status(
            result.diagnostics.backend_status
        ),
        cut_patterns=(candidate.cut_patterns if candidate is not None else ()),
        objective_value=(
            candidate.objective_value if candidate is not None else None
        ),
        diagnostics=result.diagnostics,
    )


def _unsupported_solution() -> Solution:
    return Solution(
        solution_status=SolutionStatus.UNSUPPORTED,
        cut_patterns=(),
        objective_value=None,
        diagnostics=Diagnostics(
            termination_reason=TerminationReason.UNSUPPORTED_PROBLEM,
            backend_status=BackendStatus.NOT_RUN,
            elapsed_time=0.0,
            patterns_generated=0,
            iterations=0,
            completed_objective_stages=(),
            lower_bound=None,
            upper_bound=None,
        ),
    )


def solve(
    problem: Problem,
    options: SolveOptions | None = None,
) -> Solution:

    # Validation intentionally precedes option handling and backend selection.
    validate_problem(problem)

    effective_options = options or SolveOptions()
    backend = _select_backend(effective_options.backend, _BACKENDS)
    backend_options = _to_backend_options(effective_options)

    availability = backend.check_availability()
    if not availability.available:
        raise BackendUnavailableError(
            availability.reason
            or f"Backend {backend.backend_id!r} is unavailable."
        )

    support = backend.check_support(problem, backend_options)
    if not support.supported:
        return _unsupported_solution()

    result = backend.solve(problem, backend_options)
    return _to_public_solution(result)
