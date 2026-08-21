from dataclasses import dataclass
from enum import Enum
from collections.abc import Mapping
from types import MappingProxyType

@dataclass(frozen=True, slots=True)
class Stock:
    stock_id: str
    material_id: str
    length: int
    quantity: int

@dataclass(frozen=True, slots=True)
class Demand:
    demand_id: str
    material_id: str
    length: int
    quantity: int

@dataclass(frozen=True, slots=True)
class Problem:

    #tuple[x,...] = zero-or-more x instances
    stocks: tuple[Stock, ...]
    demands: tuple[Demand, ...]

    def __post_init__(self) -> None:
        #'frozen' prevents changing the attribute value, but does not prevent
        # adding another stock into the tuple, this creates a copy of the tuple
        # in order to not reference the original tuple
        object.__setattr__(self, "stocks", tuple(self.stocks))
        object.__setattr__(self, "demands", tuple(self.demands))

@dataclass(frozen=True, slots=True)
class CutPattern:
    stock_id: str
    piece_counts: Mapping[str, int] # format is demand_id :  int
    repetitions: int

    # makes the piece_counts immutable
    def __post_init__(self) -> None:
        # checks that all the counts are non-negative
        for demand_id, count in self.piece_counts.items():
            if count < 0:
                raise ValueError(f"Negative piece count for demand {demand_id!r}")

        normalized_counts = {
            demand_id: count
            for demand_id, count in self.piece_counts.items()
            if count > 0
        }
        object.__setattr__(self, "piece_counts", MappingProxyType(normalized_counts))

    # returns a hashable tuple that can be used for dict keys, sets, etc...
    def identity_key(self) -> tuple[str, tuple[tuple[str, int], ...]]:
        return self.stock_id, tuple(sorted(self.piece_counts.items()))

# what saw can claim about the final result after the verification and proof checks
class SolutionStatus(str, Enum):
    OPTIMAL = 'optimal'
    FEASIBLE = 'feasible'
    INFEASIBLE = 'infeasible'
    LIMIT = 'limit'
    UNSUPPORTED = 'unsupported'
    ERROR = 'error'

# structured reason why the solving process stopped
class TerminationReason(str, Enum):
    COMPLETED = "completed"

    TIME_LIMIT = "time_limit"
    NODE_LIMIT = "node_limit"
    ITERATION_LIMIT = "iteration_limit"
    PATTERN_LIMIT = "pattern_limit"
    SIZE_LIMIT = "size_limit"
    CANCELLED = "cancelled"

    UNSUPPORTED_PROBLEM = "unsupported_problem"
    BACKEND_UNAVAILABLE = "backend_unavailable"

    BACKEND_FAILURE = "backend_failure"
    UNKNOWN_BACKEND_STATUS = "unknown_backend_status"
    INTERNAL_ERROR = "internal_error"

# backend's outcome before saw verifies and translates it
class BackendStatus(str, Enum):
    NOT_RUN = "not_run"
    OPTIMAL = "optimal"
    FEASIBLE = "feasible"
    INFEASIBLE = "infeasible"
    UNBOUNDED = "unbounded"
    LIMIT_REACHED = "limit_reached"
    NOT_SOLVED = "not_solved"
    FAILED = "failed"
    UNKNOWN = "unknown"

# a named step in saw's lexicographic optimization sequence
class ObjectiveStage(str, Enum):
    STOCK_PIECE_COUNT = "stock_piece_count"
    TOTAL_LEFTOVER = "total_leftover"
    DISTINCT_PATTERN_COUNT = "distinct_pattern_count"

@dataclass(frozen=True, slots=True)
class ObjectiveValue:
    stock_piece_count: int
    total_leftover: int
    distinct_pattern_count: int

@dataclass(frozen=True, slots=True)
class Diagnostics:
    termination_reason: TerminationReason
    backend_status: BackendStatus
    elapsed_time: float
    patterns_generated: int
    iterations: int
    completed_objective_stages: tuple[ObjectiveStage, ...]
    lower_bound: int | None
    upper_bound: int | None

@dataclass(frozen=True, slots=True)
class Solution:
    solution_status: SolutionStatus
    cut_patterns: tuple[CutPattern, ...]
    objective_value: ObjectiveValue | None
    diagnostics: Diagnostics

    def __post_init__(self) -> None:
        object.__setattr__(self, "cut_patterns", tuple(self.cut_patterns))

# represents the result of solution verification
class VerificationResult(str, Enum):
    VALID = "valid"
    INVALID = "invalid"

# enum with every verification failure
class VerificationFindingCode(str, Enum):
    UNKNOWN_STOCK_ID = "unknown_stock_id"
    UNKNOWN_DEMAND_ID = "unknown_demand_id"
    NON_POSITIVE_REPETITIONS = "non_positive_repetitions"
    EMPTY_PATTERN = "empty_pattern"
    DUPLICATE_PATTERN = "duplicate_pattern"
    PATTERN_CAPACITY_EXCEEDED = "pattern_capacity_exceeded"
    MATERIAL_MISMATCH = "material_mismatch"
    STOCK_QUANTITY_EXCEEDED = "stock_quantity_exceeded"
    DEMAND_UNDERPRODUCED = "demand_underproduced"
    DEMAND_OVERPRODUCED = "demand_overproduced"
    OBJECTIVE_MISMATCH = "objective_mismatch"

# represents one specific problem found during verification
@dataclass(frozen=True, slots=True)
class VerificationFinding:
    code: VerificationFindingCode
    message: str
    stock_id: str | None = None
    demand_id: str | None = None
    pattern_index: int | None = None

# collects all of the problems found during the verification
@dataclass(frozen=True, slots=True)
class VerificationReport:
    findings: tuple[VerificationFinding, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "findings", tuple(self.findings))

    # returns VALID if findings are empty, INVALID if not
    @property
    def result(self) -> VerificationResult:
        if self.findings:
            return VerificationResult.INVALID
        return VerificationResult.VALID

    @property
    def is_valid(self) -> bool:
        return not self.findings