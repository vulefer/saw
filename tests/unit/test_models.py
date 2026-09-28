from dataclasses import FrozenInstanceError, fields

import pytest

from saw import (
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
from saw.patterns import Pattern


def test_stock_exposes_domain_values_directly() -> None:
    stock = Stock("stock-10", "steel", 10, 2)

    assert stock.stock_id == "stock-10"
    assert stock.material_id == "steel"
    assert stock.length == 10
    assert stock.quantity == 2


def test_demand_exposes_domain_values_directly() -> None:
    demand = Demand("demand-6", "steel", 6, 3)

    assert demand.demand_id == "demand-6"
    assert demand.material_id == "steel"
    assert demand.length == 6
    assert demand.quantity == 3


@pytest.mark.parametrize(
    ("model", "field_name", "new_value"),
    [
        (Stock("stock-10", "steel", 10, 2), "length", 12),
        (Demand("demand-6", "steel", 6, 3), "quantity", 4),
    ],
)
def test_models_cannot_be_mutated(model: object, field_name: str, new_value: object) -> None:
    with pytest.raises(FrozenInstanceError):
        setattr(model, field_name, new_value)


def test_problem_takes_an_immutable_snapshot_of_collections() -> None:
    stock = Stock("stock-10", "steel", 10, 1)
    demand = Demand("demand-6", "steel", 6, 1)
    stocks = [stock]
    demands = [demand]

    problem = Problem(stocks, demands)
    stocks.clear()
    demands.clear()

    assert problem.stocks == (stock,)
    assert problem.demands == (demand,)
    assert isinstance(problem.stocks, tuple)
    assert isinstance(problem.demands, tuple)


def test_models_have_only_generic_domain_fields() -> None:
    assert [field.name for field in fields(Stock)] == [
        "stock_id",
        "material_id",
        "length",
        "quantity",
    ]
    assert [field.name for field in fields(Demand)] == [
        "demand_id",
        "material_id",
        "length",
        "quantity",
    ]
    assert [field.name for field in fields(Problem)] == ["stocks", "demands"]


def test_pattern_takes_an_immutable_snapshot_of_piece_counts() -> None:
    piece_counts = {"demand-6": 2, "demand-4": 1}

    pattern = Pattern("stock-10", piece_counts)
    piece_counts["demand-6"] = 99

    assert dict(pattern.piece_counts) == {"demand-6": 2, "demand-4": 1}
    with pytest.raises(TypeError):
        pattern.piece_counts["demand-6"] = 4
    with pytest.raises(FrozenInstanceError):
        pattern.stock_id = "stock-12"


def test_pattern_identity_is_canonical_and_hashable() -> None:
    first = Pattern(
        "stock-10",
        {"demand-6": 2, "demand-4": 1, "unused-demand": 0},
    )
    reordered = Pattern(
        "stock-10",
        {"demand-4": 1, "demand-6": 2},
    )

    assert first == reordered
    assert hash(first) == hash(reordered)
    assert {first, reordered} == {first}
    assert {first: "solver-variable"}[reordered] == "solver-variable"
    assert dict(first.piece_counts) == {"demand-4": 1, "demand-6": 2}


def test_pattern_identity_includes_stock_id() -> None:
    first = Pattern("stock-10", {"demand-6": 1})
    second = Pattern("stock-12", {"demand-6": 1})

    assert first != second


def test_pattern_rejects_negative_piece_counts() -> None:
    with pytest.raises(ValueError, match="demand-6"):
        Pattern("stock-10", {"demand-6": -1})


def test_cut_pattern_keeps_repetitions_separate_from_pattern_identity() -> None:
    pattern = Pattern("stock-10", {"demand-6": 1})

    used_once = CutPattern(pattern, 1)
    used_twice = CutPattern(pattern, 2)

    assert used_once != used_twice
    assert used_once.pattern is pattern
    assert used_once.stock_id == "stock-10"
    assert dict(used_once.piece_counts) == {"demand-6": 1}


def test_objective_value_exposes_separate_named_components() -> None:
    objective = ObjectiveValue(
        stock_piece_count=3,
        total_leftover=8,
        distinct_pattern_count=2,
    )

    assert objective.stock_piece_count == 3
    assert objective.total_leftover == 8
    assert objective.distinct_pattern_count == 2
    assert [field.name for field in fields(ObjectiveValue)] == [
        "stock_piece_count",
        "total_leftover",
        "distinct_pattern_count",
    ]


def test_public_result_enums_expose_structured_values() -> None:
    assert {status.value for status in SolutionStatus} == {
        "optimal",
        "feasible",
        "infeasible",
        "limit",
        "unsupported",
        "error",
    }
    assert TerminationReason.TIME_LIMIT.value == "time_limit"
    assert BackendStatus.UNKNOWN.value == "unknown"
    assert tuple(ObjectiveStage) == (
        ObjectiveStage.STOCK_PIECE_COUNT,
        ObjectiveStage.TOTAL_LEFTOVER,
        ObjectiveStage.DISTINCT_PATTERN_COUNT,
    )


def test_diagnostics_exposes_structured_solver_information() -> None:
    diagnostics = Diagnostics(
        termination_reason=TerminationReason.TIME_LIMIT,
        backend_status=BackendStatus.LIMIT_REACHED,
        elapsed_time=1.25,
        patterns_generated=12,
        iterations=4,
        completed_objective_stages=(ObjectiveStage.STOCK_PIECE_COUNT,),
        lower_bound=3,
        upper_bound=4,
    )

    assert diagnostics.termination_reason is TerminationReason.TIME_LIMIT
    assert diagnostics.backend_status is BackendStatus.LIMIT_REACHED
    assert diagnostics.completed_objective_stages == (ObjectiveStage.STOCK_PIECE_COUNT,)
    assert isinstance(diagnostics.elapsed_time, float)


def test_solution_takes_an_immutable_snapshot_of_patterns() -> None:
    pattern = CutPattern(Pattern("stock-10", {"demand-6": 1}), 2)
    patterns = [pattern]
    objective = ObjectiveValue(2, 8, 1)
    diagnostics = Diagnostics(
        termination_reason=TerminationReason.COMPLETED,
        backend_status=BackendStatus.OPTIMAL,
        elapsed_time=0.5,
        patterns_generated=3,
        iterations=1,
        completed_objective_stages=tuple(ObjectiveStage),
        lower_bound=2,
        upper_bound=2,
    )

    solution = Solution(
        solution_status=SolutionStatus.OPTIMAL,
        cut_patterns=patterns,
        objective_value=objective,
        diagnostics=diagnostics,
    )
    patterns.clear()

    assert solution.solution_status is SolutionStatus.OPTIMAL
    assert solution.cut_patterns == (pattern,)
    assert isinstance(solution.cut_patterns, tuple)
    assert solution.objective_value is objective
    assert solution.diagnostics is diagnostics


def test_solution_can_represent_an_outcome_without_a_plan() -> None:
    diagnostics = Diagnostics(
        termination_reason=TerminationReason.COMPLETED,
        backend_status=BackendStatus.INFEASIBLE,
        elapsed_time=0.2,
        patterns_generated=0,
        iterations=1,
        completed_objective_stages=(),
        lower_bound=None,
        upper_bound=None,
    )

    solution = Solution(
        solution_status=SolutionStatus.INFEASIBLE,
        cut_patterns=(),
        objective_value=None,
        diagnostics=diagnostics,
    )

    assert solution.cut_patterns == ()
    assert solution.objective_value is None


def test_verification_result_exposes_stable_values() -> None:
    assert {result.value for result in VerificationResult} == {"valid", "invalid"}


def test_verification_finding_codes_expose_stable_values() -> None:
    assert {code.value for code in VerificationFindingCode} == {
        "unknown_stock_id",
        "unknown_demand_id",
        "non_positive_repetitions",
        "empty_pattern",
        "malformed_pattern",
        "duplicate_pattern",
        "pattern_capacity_exceeded",
        "material_mismatch",
        "stock_quantity_exceeded",
        "demand_underproduced",
        "demand_overproduced",
        "objective_mismatch",
    }


def test_verification_finding_exposes_message_and_optional_context() -> None:
    finding = VerificationFinding(
        code=VerificationFindingCode.MATERIAL_MISMATCH,
        message="Demand material does not match stock material.",
        stock_id="stock-10",
        demand_id="demand-6",
        pattern_index=2,
    )

    assert finding.code is VerificationFindingCode.MATERIAL_MISMATCH
    assert finding.message == "Demand material does not match stock material."
    assert finding.stock_id == "stock-10"
    assert finding.demand_id == "demand-6"
    assert finding.pattern_index == 2


def test_verification_finding_context_defaults_to_none() -> None:
    finding = VerificationFinding(
        code=VerificationFindingCode.OBJECTIVE_MISMATCH,
        message="Reported objective does not match the verified objective.",
    )

    assert finding.stock_id is None
    assert finding.demand_id is None
    assert finding.pattern_index is None


def test_verification_finding_cannot_be_mutated() -> None:
    finding = VerificationFinding(
        code=VerificationFindingCode.EMPTY_PATTERN,
        message="Pattern contains no pieces.",
    )

    with pytest.raises(FrozenInstanceError):
        finding.message = "changed"


def test_empty_verification_report_is_valid() -> None:
    report = VerificationReport(findings=())

    assert report.result is VerificationResult.VALID
    assert report.is_valid is True


def test_verification_report_with_findings_is_invalid() -> None:
    findings = (
        VerificationFinding(
            code=VerificationFindingCode.UNKNOWN_STOCK_ID,
            message="Unknown stock.",
            stock_id="missing-stock",
            pattern_index=0,
        ),
        VerificationFinding(
            code=VerificationFindingCode.DEMAND_UNDERPRODUCED,
            message="Demand was not fully produced.",
            demand_id="demand-6",
        ),
    )

    report = VerificationReport(findings=findings)

    assert report.findings == findings
    assert report.result is VerificationResult.INVALID
    assert report.is_valid is False


def test_verification_report_takes_an_immutable_snapshot_of_findings() -> None:
    finding = VerificationFinding(
        code=VerificationFindingCode.EMPTY_PATTERN,
        message="Pattern contains no pieces.",
        pattern_index=1,
    )
    findings = [finding]

    report = VerificationReport(findings=findings)
    findings.clear()

    assert report.findings == (finding,)
    assert isinstance(report.findings, tuple)
    with pytest.raises(FrozenInstanceError):
        report.findings = ()
