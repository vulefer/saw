"""The supported public interface for :mod:`saw`."""

from saw.exceptions import (
    BackendUnavailableError,
    InternalVerificationError,
    SawError,
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
    "solve",
    "validate_problem",
    "verify_solution",
)
