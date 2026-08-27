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
    VerificationReport,
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


def _make_multi_material_problem() -> Problem:
    return Problem(
        stocks=(Stock("stock-10", "steel", 10, 3),),
        demands=(
            Demand("demand-steel-6", "steel", 6, 3),
            Demand("demand-aluminum-4", "aluminum", 4, 3),
            Demand("demand-wood-2", "wood", 2, 3),
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


def _verify_without_demand_production(
    problem: Problem,
    solution: Solution,
) -> VerificationReport:
    """Keep checks from earlier verification stages independently testable."""
    later_verification_codes = {
        VerificationFindingCode.DEMAND_UNDERPRODUCED,
        VerificationFindingCode.DEMAND_OVERPRODUCED,
        VerificationFindingCode.OBJECTIVE_MISMATCH,
    }
    report = verify_solution(problem, solution)
    return VerificationReport(
        findings=tuple(
            finding
            for finding in report.findings
            if finding.code not in later_verification_codes
        )
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

    report = _verify_without_demand_production(_make_problem(), solution)

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

    report = _verify_without_demand_production(_make_problem(), solution)

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

    report = _verify_without_demand_production(_make_problem(), solution)

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

    report = _verify_without_demand_production(_make_problem(), solution)

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

    report = _verify_without_demand_production(_make_problem(), solution)

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

    report = _verify_without_demand_production(_make_problem(), solution)

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

    report = _verify_without_demand_production(_make_problem(), solution)

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
        CutPattern(pattern=pattern, repetitions=1),
        CutPattern(pattern=pattern, repetitions=1),
    )

    report = _verify_without_demand_production(_make_problem(), solution)

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

    report = _verify_without_demand_production(_make_problem(), solution)

    assert report.is_valid
    assert report.findings == ()


def test_pattern_with_unused_capacity_is_accepted() -> None:
    solution = _make_solution(
        CutPattern(
            pattern=Pattern("stock-10", {"demand-6": 1}),
            repetitions=1,
        )
    )

    report = _verify_without_demand_production(_make_problem(), solution)

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

    report = _verify_without_demand_production(_make_problem(), solution)

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

    report = _verify_without_demand_production(_make_problem(), solution)

    assert report.is_valid
    assert report.findings == ()


def test_over_capacity_pattern_is_reported() -> None:
    solution = _make_solution(
        CutPattern(
            pattern=Pattern("stock-10", {"demand-6": 1, "demand-4": 2}),
            repetitions=1,
        )
    )

    report = _verify_without_demand_production(_make_problem(), solution)

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

    report = _verify_without_demand_production(_make_problem(), solution)

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

    report = _verify_without_demand_production(_make_problem(), solution)

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

    report = _verify_without_demand_production(_make_problem(), solution)

    assert report.is_valid


def test_cross_material_cut_is_reported() -> None:
    solution = _make_solution(
        CutPattern(
            pattern=Pattern("stock-10", {"demand-aluminum-4": 1}),
            repetitions=1,
        )
    )

    report = _verify_without_demand_production(
        _make_multi_material_problem(),
        solution,
    )

    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.code is VerificationFindingCode.MATERIAL_MISMATCH
    assert finding.pattern_index == 0
    assert finding.stock_id == "stock-10"
    assert finding.demand_id == "demand-aluminum-4"
    assert "'aluminum'" in finding.message
    assert "'steel'" in finding.message


def test_every_material_mismatch_is_reported() -> None:
    solution = _make_solution(
        CutPattern(
            pattern=Pattern(
                "stock-10",
                {
                    "demand-aluminum-4": 1,
                    "demand-wood-2": 1,
                },
            ),
            repetitions=1,
        )
    )

    report = _verify_without_demand_production(
        _make_multi_material_problem(),
        solution,
    )

    assert [finding.code for finding in report.findings] == [
        VerificationFindingCode.MATERIAL_MISMATCH,
        VerificationFindingCode.MATERIAL_MISMATCH,
    ]
    assert [finding.pattern_index for finding in report.findings] == [0, 0]
    assert [finding.stock_id for finding in report.findings] == [
        "stock-10",
        "stock-10",
    ]
    assert [finding.demand_id for finding in report.findings] == [
        "demand-aluminum-4",
        "demand-wood-2",
    ]


def test_stock_usage_is_aggregated_across_patterns() -> None:
    solution = _make_solution(
        CutPattern(
            pattern=Pattern("stock-10", {"demand-6": 1}),
            repetitions=2,
        ),
        CutPattern(
            pattern=Pattern("stock-10", {"demand-4": 1}),
            repetitions=2,
        ),
    )

    report = _verify_without_demand_production(_make_problem(), solution)

    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.code is VerificationFindingCode.STOCK_QUANTITY_EXCEEDED
    assert finding.stock_id == "stock-10"
    assert finding.pattern_index is None
    assert "4 times" in finding.message
    assert "available quantity 3" in finding.message
    assert "by 1" in finding.message


def test_stock_usage_is_aggregated_separately_by_stock_id() -> None:
    solution = _make_solution(
        CutPattern(
            pattern=Pattern("stock-10", {"demand-6": 1}),
            repetitions=3,
        ),
        CutPattern(
            pattern=Pattern("stock-12", {"demand-4": 1}),
            repetitions=2,
        ),
    )

    report = _verify_without_demand_production(_make_problem(), solution)

    assert report.is_valid
    assert report.findings == ()


def test_exact_demand_production_accepts_aggregated_pattern_totals() -> None:
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
                "stock-12",
                {"demand-6": 1, "demand-4": 1},
            ),
            repetitions=2,
        ),
    )

    findings = verification_module._check_exact_demand_production(
        _make_problem(),
        solution,
    )

    assert findings == []


def test_underproduced_demand_is_reported() -> None:
    solution = _make_solution(
        CutPattern(
            pattern=Pattern("stock-10", {"demand-6": 1}),
            repetitions=3,
        )
    )

    findings = verification_module._check_exact_demand_production(
        _make_problem(),
        solution,
    )

    assert len(findings) == 1
    finding = findings[0]
    assert finding.code is VerificationFindingCode.DEMAND_UNDERPRODUCED
    assert finding.demand_id == "demand-4"
    assert finding.pattern_index is None
    assert "requires quantity 3" in finding.message
    assert "patterns produce 0" in finding.message
    assert "underproduced by 3" in finding.message


def test_overproduced_demand_is_reported() -> None:
    solution = _make_solution(
        CutPattern(
            pattern=Pattern(
                "stock-10",
                {"demand-6": 1, "demand-4": 1},
            ),
            repetitions=4,
        )
    )

    findings = verification_module._check_exact_demand_production(
        _make_problem(),
        solution,
    )

    assert [finding.code for finding in findings] == [
        VerificationFindingCode.DEMAND_OVERPRODUCED,
        VerificationFindingCode.DEMAND_OVERPRODUCED,
    ]
    assert [finding.demand_id for finding in findings] == [
        "demand-6",
        "demand-4",
    ]
    assert all(
        "requires quantity 3" in finding.message for finding in findings
    )
    assert all("patterns produce 4" in finding.message for finding in findings)
    assert all("overproduced by 1" in finding.message for finding in findings)


def test_every_demand_is_checked_when_no_pieces_are_produced() -> None:
    findings = verification_module._check_exact_demand_production(
        _make_problem(),
        _make_solution(),
    )

    assert [finding.code for finding in findings] == [
        VerificationFindingCode.DEMAND_UNDERPRODUCED,
        VerificationFindingCode.DEMAND_UNDERPRODUCED,
    ]
    assert [finding.demand_id for finding in findings] == [
        "demand-6",
        "demand-4",
    ]


def test_verify_solution_checks_exact_demand_production() -> None:
    solution = _make_solution(
        CutPattern(
            pattern=Pattern("stock-10", {"demand-6": 1}),
            repetitions=3,
        )
    )

    report = verify_solution(_make_problem(), solution)

    assert VerificationFindingCode.DEMAND_UNDERPRODUCED in {
        finding.code for finding in report.findings
    }


def test_stock_count_uses_only_positive_integer_repetitions() -> None:
    solution = _make_solution(
        CutPattern(Pattern("stock-10", {"demand-6": 1}), repetitions=3),
        CutPattern(Pattern("stock-10", {"demand-4": 1}), repetitions=0),
        CutPattern(Pattern("stock-12", {"demand-4": 1}), repetitions=-2),
        CutPattern(
            Pattern("stock-12", {"demand-6": 1}),
            repetitions=1.5,  # type: ignore[arg-type]
        ),
    )

    assert verification_module._calculate_stock_count(solution) == 3


def test_leftover_follows_the_specification_for_every_pattern_use() -> None:
    solution = _make_solution(
        CutPattern(Pattern("stock-10", {"demand-6": 1}), repetitions=2),
        CutPattern(Pattern("stock-12", {"demand-4": 1}), repetitions=1),
        CutPattern(Pattern("stock-12", {"demand-6": 1}), repetitions=0),
    )

    assert verification_module._calculate_leftover(
        _make_problem(),
        solution,
    ) == 16


def test_distinct_count_uses_canonical_positive_pattern_identity() -> None:
    solution = _make_solution(
        CutPattern(
            Pattern("stock-10", {"demand-6": 1, "demand-4": 1}),
            repetitions=1,
        ),
        CutPattern(
            Pattern(
                "stock-10",
                {"demand-4": 1, "unused": 0, "demand-6": 1},
            ),
            repetitions=2,
        ),
        CutPattern(
            Pattern("stock-12", {"demand-6": 1, "demand-4": 1}),
            repetitions=1,
        ),
        CutPattern(
            Pattern("stock-12", {"demand-4": 2}),
            repetitions=0,
        ),
    )

    assert verification_module.calculate_distinct_pattern_count(solution) == 2


def test_every_mismatched_reported_objective_component_is_reported() -> None:
    original = _make_solution(
        CutPattern(
            Pattern("stock-10", {"demand-6": 1, "demand-4": 1}),
            repetitions=3,
        )
    )
    solution = Solution(
        solution_status=original.solution_status,
        cut_patterns=original.cut_patterns,
        objective_value=ObjectiveValue(99, 98, 97),
        diagnostics=original.diagnostics,
    )

    findings = verification_module._check_objective_components(
        _make_problem(),
        solution,
    )

    assert [finding.code for finding in findings] == [
        VerificationFindingCode.OBJECTIVE_MISMATCH,
        VerificationFindingCode.OBJECTIVE_MISMATCH,
        VerificationFindingCode.OBJECTIVE_MISMATCH,
    ]
    assert "'stock_piece_count'" in findings[0].message
    assert "'total_leftover'" in findings[1].message
    assert "'distinct_pattern_count'" in findings[2].message


def test_correct_objective_components_add_no_findings() -> None:
    solution = _make_solution(
        CutPattern(
            Pattern("stock-10", {"demand-6": 1, "demand-4": 1}),
            repetitions=3,
        )
    )

    report = verify_solution(_make_problem(), solution)

    assert report.is_valid


def test_missing_objective_value_adds_no_objective_finding() -> None:
    original = _make_solution()
    solution = Solution(
        solution_status=original.solution_status,
        cut_patterns=original.cut_patterns,
        objective_value=None,
        diagnostics=original.diagnostics,
    )

    assert verification_module._check_objective_components(
        _make_problem(),
        solution,
    ) == []


def test_boolean_reported_objective_is_not_accepted_as_integer() -> None:
    original = _make_solution(
        CutPattern(
            Pattern("stock-10", {"demand-6": 1, "demand-4": 1}),
            repetitions=1,
        )
    )
    solution = Solution(
        solution_status=original.solution_status,
        cut_patterns=original.cut_patterns,
        objective_value=ObjectiveValue(
            stock_piece_count=True,  # type: ignore[arg-type]
            total_leftover=0,
            distinct_pattern_count=1,
        ),
        diagnostics=original.diagnostics,
    )

    findings = verification_module._check_objective_components(
        _make_problem(),
        solution,
    )

    assert len(findings) == 1
    assert findings[0].code is VerificationFindingCode.OBJECTIVE_MISMATCH
    assert "'stock_piece_count'" in findings[0].message
