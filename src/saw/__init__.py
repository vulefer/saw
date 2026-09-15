"""The supported public interface for :mod:`saw`."""

from saw.exceptions import (
    BackendUnavailableError,
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
from saw.serialization import problem_from_json
from saw.solve import SolveOptions, solve
from saw.validation import validate_problem
from saw.verification import verify_solution

__all__ = (
    "BackendStatus",
    "BackendUnavailableError",
    "CutPattern",
    "Demand",
    "Diagnostics",
    "InternalVerificationError",
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
    "problem_from_json",
    "solve",
    "validate_problem",
    "verify_solution",
)
