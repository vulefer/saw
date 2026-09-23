"""The supported public interface for :mod:`saw`."""

from saw.exceptions import (
    BackendUnavailableError,
    ErrorCode,
    InternalVerificationError,
    SawError,
    SerializationError,
    SerializationErrorCode,
    ValidationError,
    ValidationErrorCode,
)
from saw.models import (
    BackendStatus,
    CutPattern,
    Demand,
    Diagnostics,
    ObjectiveStage,
    ObjectiveValue,
    Problem,
    Solution,
    SolutionStatus,
    Stock,
    TerminationReason,
    VerificationFinding,
    VerificationFindingCode,
    VerificationReport,
    VerificationResult,
)
from saw.serialization import (
    json_from_error,
    json_from_solution,
    problem_from_bytes,
    problem_from_json,
)
from saw.solve import InvalidSolveOptionsError, SolveOptions, solve
from saw.validation import validate_problem
from saw.verification import verify_solution

__all__ = (
    "BackendStatus",
    "BackendUnavailableError",
    "CutPattern",
    "Demand",
    "Diagnostics",
    "ErrorCode",
    "InternalVerificationError",
    "InvalidSolveOptionsError",
    "ObjectiveStage",
    "ObjectiveValue",
    "Problem",
    "SawError",
    "SerializationError",
    "SerializationErrorCode",
    "Solution",
    "SolutionStatus",
    "SolveOptions",
    "Stock",
    "TerminationReason",
    "ValidationError",
    "ValidationErrorCode",
    "VerificationFinding",
    "VerificationFindingCode",
    "VerificationReport",
    "VerificationResult",
    "json_from_error",
    "json_from_solution",
    "problem_from_bytes",
    "problem_from_json",
    "solve",
    "validate_problem",
    "verify_solution",
)
