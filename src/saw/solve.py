from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
import math
from time import perf_counter

from saw.backends.base import (
    BackendOptions,
    OptimizationBackend,
    UnverifiedBackendResult,
    translate_backend_status,
)
from saw.backends.exact_enumerated.backend import ExactEnumeratedBackend
from saw.exceptions import BackendUnavailableError, InternalVerificationError
from saw.models import (
    BackendStatus,
    Diagnostics,
    Problem,
    Solution,
    SolutionStatus,
    TerminationReason,
)
from saw.timing import Deadline
from saw.validation import validate_problem
from saw.verification import verify_solution


# raised when a SolveOptions value is invalid
# subclasses ValueError as the docs promise, but has its own class so the cli can
# report it as invalid_cli_usage without catching unrelated ValueErrors from bugs
class InvalidSolveOptionsError(ValueError):
    pass


# the settings for one solve() call
@dataclass(frozen=True, slots=True)
class SolveOptions:
    backend: str = "exact_enumerated"
    # safe defaults so leaving an option out never means unlimited,
    # pass None to turn a limit off, see docs/resource-limits.md
    time_limit_seconds: float | None = 30.0
    pattern_limit: int | None = 50_000
    # size checks done by solve() itself before the backend is used
    problem_size_limit: int | None = 200
    numeric_magnitude_limit: int | None = 1_000_000


# every backend saw can use, by name
_BACKENDS: dict[str, OptimizationBackend] = {
    "exact_enumerated": ExactEnumeratedBackend(),
}


# raises InvalidSolveOptionsError for any bad option value (wrong type, bool,
# zero, negative, NaN, infinity) before any backend is used
# whether the backend name exists is checked later in _select_backend
def _validate_solve_options(options: SolveOptions) -> None:
    _require_backend_identifier(options.backend)
    _require_optional_finite_positive_number(
        options.time_limit_seconds,
        "time_limit_seconds",
    )
    _require_optional_positive_integer(options.pattern_limit, "pattern_limit")
    _require_optional_positive_integer(
        options.problem_size_limit,
        "problem_size_limit",
    )
    _require_optional_positive_integer(
        options.numeric_magnitude_limit,
        "numeric_magnitude_limit",
    )


# the backend name must be a non-empty string
def _require_backend_identifier(value: object) -> None:
    if not isinstance(value, str) or value == "":
        raise InvalidSolveOptionsError(
            f"Invalid backend {value!r}: backend must be a non-empty string."
        )


# None turns the limit off, otherwise it must be a finite number above 0
def _require_optional_finite_positive_number(value: object, field: str) -> None:
    if value is None:
        return
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise InvalidSolveOptionsError(
            f"Invalid {field} {value!r}: must be a finite real number greater than zero, or None."
        )


# None turns the limit off, otherwise it must be a whole number above 0
def _require_optional_positive_integer(value: object, field: str) -> None:
    # bool counts as int in python, so true/false has to be rejected separately
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InvalidSolveOptionsError(
            f"Invalid {field} {value!r}: must be a positive integer, or None."
        )


# looks up the backend by name, raises InvalidSolveOptionsError if it doesnt exist
def _select_backend(
    backend_id: str,
    backends: Mapping[str, OptimizationBackend],
) -> OptimizationBackend:
    try:
        return backends[backend_id]
    except KeyError:
        available_ids = ", ".join(sorted(backends))
        raise InvalidSolveOptionsError(
            f"Unknown backend {backend_id!r}. Available backends: {available_ids}."
        ) from None


# the part of the options the backend needs, plus the shared deadline
def _to_backend_options(
    options: SolveOptions,
    deadline: Deadline,
) -> BackendOptions:
    return BackendOptions(
        time_limit_seconds=options.time_limit_seconds,
        pattern_limit=options.pattern_limit,
        deadline=deadline,
    )


# turns the backend's result into a Solution, not verified yet
def _to_public_solution(
    result: UnverifiedBackendResult,
    *,
    backend_id: str,
    backend_version: str | None,
) -> Solution:
    candidate = result.candidate

    return Solution(
        solution_status=translate_backend_status(result.diagnostics.backend_status),
        cut_patterns=(candidate.cut_patterns if candidate is not None else ()),
        objective_value=(candidate.objective_value if candidate is not None else None),
        # added here instead of in the backend, since solve() is the one place
        # that knows them from check_availability()
        diagnostics=replace(
            result.diagnostics,
            backend_id=backend_id,
            backend_version=backend_version,
        ),
    )


# result when the backend says it cant handle this problem
def _unsupported_solution(
    *,
    backend_id: str,
    backend_version: str | None,
) -> Solution:
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
            # known here because check_availability() already ran,
            # unlike the size and time limit results below
            backend_id=backend_id,
            backend_version=backend_version,
        ),
    )


# True if there are more stock and demand records combined than the limit
def _exceeds_problem_size_limit(problem: Problem, limit: int | None) -> bool:
    if limit is None:
        return False
    return len(problem.stocks) + len(problem.demands) > limit


# True if any length or quantity is bigger than the limit
def _exceeds_numeric_magnitude_limit(problem: Problem, limit: int | None) -> bool:
    if limit is None:
        return False
    return any(stock.length > limit or stock.quantity > limit for stock in problem.stocks) or any(
        demand.length > limit or demand.quantity > limit for demand in problem.demands
    )


# result when a size limit is hit, the backend never ran so there is no candidate
# backend_status is LIMIT_REACHED and not NOT_RUN because the json schema requires
# it for every limit status, NOT_RUN is only for unsupported
def _size_limit_solution(started_at: float) -> Solution:
    return Solution(
        solution_status=SolutionStatus.LIMIT,
        cut_patterns=(),
        objective_value=None,
        diagnostics=Diagnostics(
            termination_reason=TerminationReason.SIZE_LIMIT,
            backend_status=BackendStatus.LIMIT_REACHED,
            elapsed_time=perf_counter() - started_at,
            patterns_generated=0,
            iterations=0,
            completed_objective_stages=(),
            lower_bound=None,
            upper_bound=None,
        ),
    )


# rejects problems that are obviously too big before the backend is used
# returns the limit result, or None if both size checks pass
def _check_preflight_size_limits(
    problem: Problem,
    options: SolveOptions,
    started_at: float,
) -> Solution | None:
    if _exceeds_problem_size_limit(problem, options.problem_size_limit):
        return _size_limit_solution(started_at)
    if _exceeds_numeric_magnitude_limit(problem, options.numeric_magnitude_limit):
        return _size_limit_solution(started_at)
    return None


# same as _size_limit_solution but for time running out before the backend was reached
def _time_limit_solution(started_at: float) -> Solution:
    return Solution(
        solution_status=SolutionStatus.LIMIT,
        cut_patterns=(),
        objective_value=None,
        diagnostics=Diagnostics(
            termination_reason=TerminationReason.TIME_LIMIT,
            backend_status=BackendStatus.LIMIT_REACHED,
            elapsed_time=perf_counter() - started_at,
            patterns_generated=0,
            iterations=0,
            completed_objective_stages=(),
            lower_bound=None,
            upper_bound=None,
        ),
    )


# solves a problem and returns a verified solution, steps in order:
# validate -> check options -> pick backend -> size limits -> time limit ->
# availability -> support -> backend solve -> verify
def solve(
    problem: Problem,
    options: SolveOptions | None = None,
) -> Solution:

    # the time budget starts here, before validation, see docs/resource-limits.md
    started_at = perf_counter()

    # the problem is validated before the options on purpose
    validate_problem(problem)

    effective_options = options or SolveOptions()
    _validate_solve_options(effective_options)

    backend = _select_backend(effective_options.backend, _BACKENDS)

    size_limit_solution = _check_preflight_size_limits(
        problem,
        effective_options,
        started_at,
    )
    if size_limit_solution is not None:
        return size_limit_solution

    # one shared deadline for the rest of the call, see timing.py
    deadline = Deadline.from_start(started_at, effective_options.time_limit_seconds)
    if deadline.expired():
        return _time_limit_solution(started_at)

    backend_options = _to_backend_options(effective_options, deadline)

    availability = backend.check_availability()
    if not availability.available:
        raise BackendUnavailableError(
            availability.reason or f"Backend {backend.backend_id!r} is unavailable."
        )

    support = backend.check_support(problem, backend_options)
    if not support.supported:
        return _unsupported_solution(
            backend_id=backend.backend_id,
            backend_version=availability.backend_version,
        )

    result = backend.solve(problem, backend_options)
    unverified_solution = _to_public_solution(
        result,
        backend_id=backend.backend_id,
        backend_version=availability.backend_version,
    )

    # optimal and feasible must come with a candidate, and every candidate
    # (including one returned with a limit) must pass verification
    backend_status = result.diagnostics.backend_status
    statuses_requiring_candidate = {
        BackendStatus.OPTIMAL,
        BackendStatus.FEASIBLE,
    }
    statuses_requiring_verification = {
        *statuses_requiring_candidate,
        BackendStatus.LIMIT_REACHED,
    }

    if backend_status in statuses_requiring_candidate and result.candidate is None:
        raise InternalVerificationError(
            f"Backend reported {backend_status.value!r} without a candidate."
        )

    # nothing to verify, e.g. infeasible or a limit without a candidate
    if result.candidate is None or backend_status not in statuses_requiring_verification:
        return unverified_solution

    # any finding means the answer is not trusted and is never returned
    verification_report = verify_solution(problem, unverified_solution)
    if not verification_report.is_valid:
        finding_codes = ", ".join(finding.code.value for finding in verification_report.findings)
        raise InternalVerificationError(
            f"The backend result failed independent verification: {finding_codes}."
        )

    return replace(
        unverified_solution,
        verification_report=verification_report,
    )
