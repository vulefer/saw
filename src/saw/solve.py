from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
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


@dataclass(frozen=True, slots=True)
class SolveOptions:
    # backend selection wrapper

    backend: str = "exact_enumerated"
    time_limit_seconds: float | None = None
    pattern_limit: int | None = None
    # Preflight size guards, checked before backend availability, support,
    # or pattern enumeration. Unlike time_limit_seconds and pattern_limit,
    # these two are enforced by solve() itself (see docs/resource-limits.md),
    # so their safe defaults are already active by default.
    problem_size_limit: int | None = 200
    numeric_magnitude_limit: int | None = 1_000_000


_BACKENDS: dict[str, OptimizationBackend] = {
    "exact_enumerated": ExactEnumeratedBackend(),
}


def _validate_solve_options(options: SolveOptions) -> None:
    """Reject nonsensical or unsafe option values before backend selection.

    This runs after ``validate_problem`` and before the backend registry is
    consulted, so a malformed option can never reach ``check_availability``
    or ``check_support``. It intentionally does not check whether
    ``options.backend`` names a *registered* backend: that lookup stays in
    ``_select_backend`` so unknown-backend handling remains deterministic
    and unaffected by this check.

    Every rejection raises ``ValueError``, matching the resource-limit
    contract in docs/resource-limits.md: booleans are not numbers, and
    zero, negative, non-finite, fractional-for-an-integer-limit, or
    wrong-typed values are all invalid configuration, never a solution
    result.
    """
    _require_backend_identifier(options.backend)
    _require_optional_finite_positive_number(
        options.time_limit_seconds, "time_limit_seconds",
    )
    _require_optional_positive_integer(options.pattern_limit, "pattern_limit")
    _require_optional_positive_integer(
        options.problem_size_limit, "problem_size_limit",
    )
    _require_optional_positive_integer(
        options.numeric_magnitude_limit, "numeric_magnitude_limit",
    )


def _require_backend_identifier(value: object) -> None:
    if not isinstance(value, str) or value == "":
        raise ValueError(
            f"Invalid backend {value!r}: backend must be a non-empty string."
        )


def _require_optional_finite_positive_number(value: object, field: str) -> None:
    # None disables the limit; every other invalid shape is rejected here so
    # NaN, infinity, booleans, zero, negative values, and wrong types never
    # reach backend selection.
    if value is None:
        return
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(
            f"Invalid {field} {value!r}: must be a finite real number "
            "greater than zero, or None."
        )


def _require_optional_positive_integer(value: object, field: str) -> None:
    # bool is a subclass of int, so it must be rejected explicitly.
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(
            f"Invalid {field} {value!r}: must be a positive integer, or "
            "None."
        )


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


def _to_backend_options(
    options: SolveOptions,
    deadline: Deadline,
) -> BackendOptions:
    return BackendOptions(
        time_limit_seconds=options.time_limit_seconds,
        pattern_limit=options.pattern_limit,
        deadline=deadline,
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


def _exceeds_problem_size_limit(problem: Problem, limit: int | None) -> bool:
    if limit is None:
        return False
    return len(problem.stocks) + len(problem.demands) > limit


def _exceeds_numeric_magnitude_limit(problem: Problem, limit: int | None) -> bool:
    if limit is None:
        return False
    for stock in problem.stocks:
        if stock.length > limit or stock.quantity > limit:
            return True
    for demand in problem.demands:
        if demand.length > limit or demand.quantity > limit:
            return True
    return False


def _size_limit_solution(started_at: float) -> Solution:
    # Mirrors _unsupported_solution(): a Saw-level preflight outcome that
    # never touches a backend, so backend_status stays NOT_RUN and no
    # candidate or objective stage is claimed. Per docs/resource-limits.md,
    # problem_size_limit and numeric_magnitude_limit share the SIZE_LIMIT
    # termination reason; both are pre-solve size guards and neither implies
    # infeasibility.
    return Solution(
        solution_status=SolutionStatus.LIMIT,
        cut_patterns=(),
        objective_value=None,
        diagnostics=Diagnostics(
            termination_reason=TerminationReason.SIZE_LIMIT,
            backend_status=BackendStatus.NOT_RUN,
            elapsed_time=perf_counter() - started_at,
            patterns_generated=0,
            iterations=0,
            completed_objective_stages=(),
            lower_bound=None,
            upper_bound=None,
        ),
    )


def _check_preflight_size_limits(
    problem: Problem,
    options: SolveOptions,
    started_at: float,
) -> Solution | None:
    """Reject obviously oversized problems before enumeration or model
    construction.

    Runs after domain validation, option validation, and backend selection,
    and before backend availability, support, or ``solve``. Evaluation order
    matches docs/resource-limits.md: ``problem_size_limit`` is checked before
    ``numeric_magnitude_limit``. Returns ``None`` when neither guard is
    exceeded, so the caller proceeds to the backend unchanged.
    """
    if _exceeds_problem_size_limit(problem, options.problem_size_limit):
        return _size_limit_solution(started_at)
    if _exceeds_numeric_magnitude_limit(problem, options.numeric_magnitude_limit):
        return _size_limit_solution(started_at)
    return None


def _time_limit_solution(started_at: float) -> Solution:
    """Return the LIMIT/TIME_LIMIT outcome for a deadline already exhausted
    before the backend could be reached.

    Mirrors _size_limit_solution: a Saw-level preflight outcome that never
    touches a backend, so backend_status stays NOT_RUN and no candidate or
    objective stage is claimed. Per docs/resource-limits.md, "remaining
    end-to-end time" is checked immediately after the problem_size_limit and
    numeric_magnitude_limit preflight guards and before pattern_limit (which
    is only ever consulted while enumerating, inside the backend).
    """
    return Solution(
        solution_status=SolutionStatus.LIMIT,
        cut_patterns=(),
        objective_value=None,
        diagnostics=Diagnostics(
            termination_reason=TerminationReason.TIME_LIMIT,
            backend_status=BackendStatus.NOT_RUN,
            elapsed_time=perf_counter() - started_at,
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

    # The end-to-end budget starts on entry, before problem validation,
    # per docs/resource-limits.md, so a preflight size-limit outcome can
    # still report a meaningful elapsed_time.
    started_at = perf_counter()

    # Validation intentionally precedes option handling and backend selection.
    validate_problem(problem)

    effective_options = options or SolveOptions()
    _validate_solve_options(effective_options)

    backend = _select_backend(effective_options.backend, _BACKENDS)

    size_limit_solution = _check_preflight_size_limits(
        problem, effective_options, started_at,
    )
    if size_limit_solution is not None:
        return size_limit_solution

    # One shared, monotonic deadline for the rest of this call: pattern
    # enumeration and all three lexicographic objective stages recompute
    # their remaining time against it instead of each being handed the
    # originally configured time_limit_seconds again. Per
    # docs/resource-limits.md, "remaining end-to-end time" is the next
    # check after the size preflight guards above, before pattern_limit
    # (checked only later, while the backend enumerates).
    deadline = Deadline.from_start(started_at, effective_options.time_limit_seconds)
    if deadline.expired():
        return _time_limit_solution(started_at)

    backend_options = _to_backend_options(effective_options, deadline)

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
    unverified_solution = _to_public_solution(result)

    backend_status = result.diagnostics.backend_status
    statuses_requiring_candidate = {
        BackendStatus.OPTIMAL,
        BackendStatus.FEASIBLE,
    }
    statuses_requiring_verification = {
        *statuses_requiring_candidate,
        BackendStatus.LIMIT_REACHED,
    }

    if (
        backend_status in statuses_requiring_candidate
        and result.candidate is None
    ):
        raise InternalVerificationError(
            f"Backend reported {backend_status.value!r} without a candidate."
        )

    if (
        result.candidate is None
        or backend_status not in statuses_requiring_verification
    ):
        return unverified_solution

    verification_report = verify_solution(problem, unverified_solution)
    if not verification_report.is_valid:
        finding_codes = ", ".join(
            finding.code.value for finding in verification_report.findings
        )
        raise InternalVerificationError(
            "The backend result failed independent verification: "
            f"{finding_codes}."
        )

    return replace(
        unverified_solution,
        verification_report=verification_report,
    )
