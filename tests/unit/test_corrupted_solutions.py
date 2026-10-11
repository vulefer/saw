import pytest

from saw import verify_solution
from saw.models import (
    BackendStatus,
    CutPattern,
    Demand,
    Diagnostics,
    ObjectiveValue,
    Problem,
    Solution,
    SolutionStatus,
    Stock,
    TerminationReason,
    VerificationFindingCode,
)
from saw.patterns import Pattern


def _solution(
    *cut_patterns: CutPattern,
    objective: ObjectiveValue,
) -> Solution:
    return Solution(
        solution_status=SolutionStatus.FEASIBLE,
        cut_patterns=cut_patterns,
        objective_value=objective,
        diagnostics=Diagnostics(
            termination_reason=TerminationReason.COMPLETED,
            backend_status=BackendStatus.FEASIBLE,
            elapsed_time=0.0,
            patterns_generated=len(cut_patterns),
            iterations=1,
            completed_objective_stages=(),
            lower_bound=None,
            upper_bound=None,
        ),
    )


def _finding_codes(
    problem: Problem,
    solution: Solution,
) -> list[VerificationFindingCode]:
    return [finding.code for finding in verify_solution(problem, solution).findings]


def test_deliberately_over_capacity_solution_is_rejected() -> None:
    problem = Problem(
        stocks=(Stock("steel-10", "steel", 10, 2),),
        demands=(
            Demand("part-6", "steel", 6, 1),
            Demand("part-5", "steel", 5, 1),
            Demand("part-1", "steel", 1, 1),
        ),
    )
    solution = _solution(
        CutPattern(
            Pattern("steel-10", {"part-6": 1, "part-5": 1}),
            repetitions=1,
        ),
        CutPattern(
            Pattern("steel-10", {"part-1": 1}),
            repetitions=1,
        ),
        objective=ObjectiveValue(2, 8, 2),
    )

    assert _finding_codes(problem, solution) == [VerificationFindingCode.PATTERN_CAPACITY_EXCEEDED]


def test_deliberately_wrong_material_solution_is_rejected() -> None:
    problem = Problem(
        stocks=(Stock("steel-10", "steel", 10, 1),),
        demands=(Demand("aluminum-part", "aluminum", 4, 1),),
    )
    solution = _solution(
        CutPattern(
            Pattern("steel-10", {"aluminum-part": 1}),
            repetitions=1,
        ),
        objective=ObjectiveValue(1, 6, 1),
    )

    assert _finding_codes(problem, solution) == [VerificationFindingCode.MATERIAL_MISMATCH]


def test_deliberately_exceeded_stock_quantity_is_rejected() -> None:
    problem = Problem(
        stocks=(Stock("steel-10", "steel", 10, 1),),
        demands=(Demand("part-6", "steel", 6, 2),),
    )
    solution = _solution(
        CutPattern(
            Pattern("steel-10", {"part-6": 1}),
            repetitions=2,
        ),
        objective=ObjectiveValue(2, 8, 1),
    )

    assert _finding_codes(problem, solution) == [VerificationFindingCode.STOCK_QUANTITY_EXCEEDED]


@pytest.mark.parametrize(
    ("required_quantity", "repetitions", "expected_code"),
    [
        (2, 1, VerificationFindingCode.DEMAND_UNDERPRODUCED),
        (1, 2, VerificationFindingCode.DEMAND_OVERPRODUCED),
    ],
    ids=("underproduction", "overproduction"),
)
def test_deliberately_inexact_production_is_rejected(
    required_quantity: int,
    repetitions: int,
    expected_code: VerificationFindingCode,
) -> None:
    problem = Problem(
        stocks=(Stock("steel-10", "steel", 10, 2),),
        demands=(Demand("part-6", "steel", 6, required_quantity),),
    )
    solution = _solution(
        CutPattern(
            Pattern("steel-10", {"part-6": 1}),
            repetitions=repetitions,
        ),
        objective=ObjectiveValue(repetitions, 4 * repetitions, 1),
    )

    assert _finding_codes(problem, solution) == [expected_code]


@pytest.mark.parametrize(
    ("problem", "pattern", "expected_code"),
    [
        (
            Problem(
                stocks=(Stock("steel-10", "steel", 10, 1),),
                demands=(Demand("part-6", "steel", 6, 1),),
            ),
            Pattern("missing-stock", {"part-6": 1}),
            VerificationFindingCode.UNKNOWN_STOCK_ID,
        ),
        (
            Problem(
                stocks=(Stock("steel-10", "steel", 10, 1),),
                demands=(),
            ),
            Pattern("steel-10", {"missing-demand": 1}),
            VerificationFindingCode.UNKNOWN_DEMAND_ID,
        ),
    ],
    ids=("unknown-stock", "unknown-demand"),
)
def test_deliberately_unknown_ids_are_rejected(
    problem: Problem,
    pattern: Pattern,
    expected_code: VerificationFindingCode,
) -> None:
    solution = _solution(
        CutPattern(pattern, repetitions=1),
        objective=ObjectiveValue(1, 0, 1),
    )

    assert _finding_codes(problem, solution) == [expected_code]


@pytest.mark.parametrize(
    ("objective", "component_name"),
    [
        (ObjectiveValue(99, 0, 2), "stock_piece_count"),
        (ObjectiveValue(2, 99, 2), "total_leftover"),
        (ObjectiveValue(2, 0, 99), "distinct_pattern_count"),
    ],
    ids=("stock-count", "leftover", "distinct-pattern-count"),
)
def test_deliberately_incorrect_metrics_are_rejected(
    objective: ObjectiveValue,
    component_name: str,
) -> None:
    problem, cut_patterns = _valid_manual_plan()
    solution = _solution(*cut_patterns, objective=objective)

    report = verify_solution(problem, solution)

    assert [finding.code for finding in report.findings] == [
        VerificationFindingCode.OBJECTIVE_MISMATCH
    ]
    assert component_name in report.findings[0].message


def _valid_manual_plan() -> tuple[Problem, tuple[CutPattern, ...]]:
    problem = Problem(
        stocks=(
            Stock("steel-10", "steel", 10, 2),
            Stock("aluminum-8", "aluminum", 8, 1),
        ),
        demands=(
            Demand("steel-part-6", "steel", 6, 1),
            Demand("steel-part-4", "steel", 4, 1),
            Demand("aluminum-part-8", "aluminum", 8, 1),
        ),
    )
    cut_patterns = (
        CutPattern(
            Pattern(
                "steel-10",
                {"steel-part-6": 1, "steel-part-4": 1},
            ),
            repetitions=1,
        ),
        CutPattern(
            Pattern("aluminum-8", {"aluminum-part-8": 1}),
            repetitions=1,
        ),
    )
    return problem, cut_patterns


def test_valid_manually_constructed_solution_passes_verification() -> None:
    problem, cut_patterns = _valid_manual_plan()
    solution = _solution(
        *cut_patterns,
        objective=ObjectiveValue(2, 0, 2),
    )

    report = verify_solution(problem, solution)

    assert report.is_valid
    assert report.findings == ()
