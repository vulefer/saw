import pytest

import saw.patterns as pattern_helpers
import saw.verification as verification_module
from saw import verify_solution as public_verify_solution
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
from saw.verification import verify_solution


def _make_problem() -> Problem:
    return Problem(
        stocks=(
            Stock("stock-10", "steel", 10, 3),
            Stock("stock-12", "steel", 12, 2),
        ),
        demands=(
            Demand("demand-6", "steel", 6, 3),
            Demand("demand-4", "steel", 4, 3),
        ),
    )


def _make_solution(*cut_patterns: CutPattern) -> Solution:
    diagnostics = Diagnostics(
        termination_reason=TerminationReason.COMPLETED,
        backend_status=BackendStatus.FEASIBLE,
        elapsed_time=0.0,
        patterns_generated=len(cut_patterns),
        iterations=1,
        completed_objective_stages=(),
        lower_bound=None,
        upper_bound=None,
    )
    return Solution(
        solution_status=SolutionStatus.FEASIBLE,
        cut_patterns=cut_patterns,
        objective_value=ObjectiveValue(
            stock_piece_count=sum(
                cut_pattern.repetitions for cut_pattern in cut_patterns
            ),
            total_leftover=0,
            distinct_pattern_count=len(cut_patterns),
        ),
        diagnostics=diagnostics,
    )


def test_valid_solution_structure_has_no_findings() -> None:
    solution = _make_solution(
        CutPattern(
            pattern=Pattern(
                "stock-10",
                {"demand-6": 1, "demand-4": 1},
            ),
            repetitions=1,
        )
    )

    report = verify_solution(_make_problem(), solution)

    assert report.is_valid
    assert report.findings == ()


def test_verify_solution_is_exposed_by_the_public_package() -> None:
    assert public_verify_solution is verify_solution


def test_unknown_stock_and_demand_ids_are_reported_together() -> None:
    solution = _make_solution(
        CutPattern(
            pattern=Pattern("missing-stock", {"missing-demand": 1}),
            repetitions=1,
        )
    )

    report = verify_solution(_make_problem(), solution)

    assert [finding.code for finding in report.findings] == [
        VerificationFindingCode.UNKNOWN_STOCK_ID,
        VerificationFindingCode.UNKNOWN_DEMAND_ID,
    ]
    assert report.findings[0].stock_id == "missing-stock"
    assert report.findings[0].pattern_index == 0
    assert report.findings[1].demand_id == "missing-demand"
    assert report.findings[1].pattern_index == 0


@pytest.mark.parametrize("repetitions", [0, -1, 1.5, True])
def test_invalid_repetition_counts_are_reported(repetitions: object) -> None:
    solution = _make_solution(
        CutPattern(
            pattern=Pattern("stock-10", {"demand-6": 1}),
            repetitions=repetitions,  # type: ignore[arg-type]
        )
    )

    report = verify_solution(_make_problem(), solution)

    assert len(report.findings) == 1
    assert report.findings[0].code is (
        VerificationFindingCode.NON_POSITIVE_REPETITIONS
    )
    assert report.findings[0].stock_id == "stock-10"
    assert report.findings[0].pattern_index == 0


@pytest.mark.parametrize("piece_counts", [{}, {"demand-6": 0}])
def test_empty_patterns_are_reported(piece_counts: dict[str, int]) -> None:
    solution = _make_solution(
        CutPattern(
            pattern=Pattern("stock-10", piece_counts),
            repetitions=1,
        )
    )

    report = verify_solution(_make_problem(), solution)

    assert len(report.findings) == 1
    assert report.findings[0].code is VerificationFindingCode.EMPTY_PATTERN
    assert report.findings[0].stock_id == "stock-10"
    assert report.findings[0].pattern_index == 0


@pytest.mark.parametrize(
    "piece_counts",
    [
        {"demand-6": 1.5},
        {"demand-6": True},
    ],
)
def test_malformed_piece_counts_are_reported(
    piece_counts: dict[str, object],
) -> None:
    solution = _make_solution(
        CutPattern(
            pattern=Pattern(
                "stock-10",
                piece_counts,  # type: ignore[arg-type]
            ),
            repetitions=1,
        )
    )

    report = verify_solution(_make_problem(), solution)

    assert len(report.findings) == 1
    assert report.findings[0].code is VerificationFindingCode.MALFORMED_PATTERN
    assert report.findings[0].stock_id == "stock-10"
    assert report.findings[0].pattern_index == 0


@pytest.mark.parametrize(
    "pattern",
    [
        Pattern("", {"demand-6": 1}),
        Pattern(1, {"demand-6": 1}),  # type: ignore[arg-type]
        Pattern([], {"demand-6": 1}),  # type: ignore[arg-type]
        Pattern("stock-10", {"": 1}),
        Pattern("stock-10", {1: 1}),  # type: ignore[dict-item]
    ],
)
def test_malformed_pattern_ids_are_reported_without_crashing(
    pattern: Pattern,
) -> None:
    solution = _make_solution(CutPattern(pattern=pattern, repetitions=1))

    report = verify_solution(_make_problem(), solution)

    assert len(report.findings) == 1
    assert report.findings[0].code is VerificationFindingCode.MALFORMED_PATTERN
    assert report.findings[0].pattern_index == 0


def test_equivalent_canonical_patterns_are_reported_as_duplicates() -> None:
    solution = _make_solution(
        CutPattern(
            pattern=Pattern(
                "stock-10",
                {"demand-6": 1, "demand-4": 1},
            ),
            repetitions=1,
        ),
        CutPattern(
            pattern=Pattern(
                "stock-10",
                {"demand-4": 1, "demand-6": 1, "unused": 0},
            ),
            repetitions=2,
        ),
    )

    report = verify_solution(_make_problem(), solution)

    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.code is VerificationFindingCode.DUPLICATE_PATTERN
    assert finding.stock_id == "stock-10"
    assert finding.pattern_index == 1
    assert "index 0" in finding.message


def test_every_duplicate_after_the_first_is_reported() -> None:
    pattern = Pattern("stock-10", {"demand-6": 1})
    solution = _make_solution(
        CutPattern(pattern=pattern, repetitions=1),
        CutPattern(pattern=pattern, repetitions=2),
        CutPattern(pattern=pattern, repetitions=3),
    )

    report = verify_solution(_make_problem(), solution)

    assert [finding.code for finding in report.findings] == [
        VerificationFindingCode.DUPLICATE_PATTERN,
        VerificationFindingCode.DUPLICATE_PATTERN,
    ]
    assert [finding.pattern_index for finding in report.findings] == [1, 2]


def test_same_piece_counts_for_different_stocks_are_not_duplicates() -> None:
    solution = _make_solution(
        CutPattern(
            pattern=Pattern("stock-10", {"demand-6": 1}),
            repetitions=1,
        ),
        CutPattern(
            pattern=Pattern("stock-12", {"demand-6": 1}),
            repetitions=1,
        ),
    )

    report = verify_solution(_make_problem(), solution)

    assert report.is_valid
    assert report.findings == ()


def test_pattern_with_unused_capacity_is_accepted() -> None:
    solution = _make_solution(
        CutPattern(
            pattern=Pattern("stock-10", {"demand-6": 1}),
            repetitions=1,
        )
    )

    report = verify_solution(_make_problem(), solution)

    assert report.is_valid
    assert report.findings == ()


def test_pattern_that_exactly_fills_stock_is_accepted() -> None:
    solution = _make_solution(
        CutPattern(
            pattern=Pattern(
                "stock-10",
                {"demand-6": 1, "demand-4": 1},
            ),
            repetitions=1,
        )
    )

    report = verify_solution(_make_problem(), solution)

    assert report.is_valid
    assert report.findings == ()


def test_pattern_capacity_uses_demand_lengths_and_piece_counts() -> None:
    solution = _make_solution(
        CutPattern(
            # The requested demand quantity is 3, but this pattern uses 2.
            # Capacity must therefore be 2 * 4 = 8, not 3 * 4 = 12.
            pattern=Pattern("stock-10", {"demand-4": 2}),
            repetitions=1,
        )
    )

    report = verify_solution(_make_problem(), solution)

    assert report.is_valid
    assert report.findings == ()


def test_over_capacity_pattern_is_reported() -> None:
    solution = _make_solution(
        CutPattern(
            pattern=Pattern("stock-10", {"demand-6": 1, "demand-4": 2}),
            repetitions=1,
        )
    )

    report = verify_solution(_make_problem(), solution)

    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.code is VerificationFindingCode.PATTERN_CAPACITY_EXCEEDED
    assert finding.stock_id == "stock-10"
    assert finding.pattern_index == 0
    assert "uses length 14" in finding.message
    assert "stock length 10" in finding.message
    assert "by 4" in finding.message


def test_pattern_capacity_is_checked_per_use_not_across_repetitions() -> None:
    solution = _make_solution(
        CutPattern(
            pattern=Pattern("stock-10", {"demand-6": 1}),
            repetitions=2,
        )
    )

    report = verify_solution(_make_problem(), solution)

    assert report.is_valid
    assert report.findings == ()


def test_malformed_pattern_does_not_prevent_other_capacity_checks() -> None:
    solution = _make_solution(
        CutPattern(
            pattern=Pattern(
                "stock-10",
                {"demand-6": 1.5},  # type: ignore[dict-item]
            ),
            repetitions=1,
        ),
        CutPattern(
            pattern=Pattern("stock-10", {"demand-6": 2}),
            repetitions=1,
        ),
    )

    report = verify_solution(_make_problem(), solution)

    assert [finding.code for finding in report.findings] == [
        VerificationFindingCode.MALFORMED_PATTERN,
        VerificationFindingCode.PATTERN_CAPACITY_EXCEEDED,
    ]
    assert report.findings[0].pattern_index == 0
    assert report.findings[1].pattern_index == 1


def test_capacity_verification_does_not_use_pattern_generators(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("production pattern generator was called")

    helper_names = (
        "enumerate_patterns_for_single_stock",
        "enumerate_patterns_for_problem",
    )
    for helper_name in helper_names:
        monkeypatch.setattr(pattern_helpers, helper_name, fail_if_called)
        monkeypatch.setattr(
            verification_module,
            helper_name,
            fail_if_called,
            raising=False,
        )

    solution = _make_solution(
        CutPattern(
            pattern=Pattern("stock-10", {"demand-6": 1}),
            repetitions=1,
        )
    )

    report = verify_solution(_make_problem(), solution)

    assert report.is_valid
