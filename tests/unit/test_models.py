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
)


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
        (Demand("demand-6", "steel", 6, 3), "quantity", 4)
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


def test_cut_pattern_takes_an_immutable_snapshot_of_piece_counts() -> None:
    piece_counts = {"demand-6": 2, "demand-4": 1}

    pattern = CutPattern("stock-10", piece_counts, 3)
    piece_counts["demand-6"] = 99

    assert dict(pattern.piece_counts) == {"demand-6": 2, "demand-4": 1}
    with pytest.raises(TypeError):
        pattern.piece_counts["demand-6"] = 4


def test_cut_pattern_identity_is_canonical() -> None:
    first = CutPattern(
        "stock-10",
        {"demand-6": 2, "demand-4": 1, "unused-demand": 0},
        1,
    )
    reordered = CutPattern(
        "stock-10",
        {"demand-4": 1, "demand-6": 2},
        7,
    )

    assert first.identity_key() == reordered.identity_key()
    assert hash(first.identity_key()) == hash(reordered.identity_key())


def test_cut_pattern_identity_includes_stock_id() -> None:
    first = CutPattern("stock-10", {"demand-6": 1}, 1)
    second = CutPattern("stock-12", {"demand-6": 1}, 1)

    assert first.identity_key() != second.identity_key()


def test_cut_pattern_rejects_negative_piece_counts() -> None:
    with pytest.raises(ValueError, match="demand-6"):
        CutPattern("stock-10", {"demand-6": -1}, 1)


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
    assert diagnostics.completed_objective_stages == (
        ObjectiveStage.STOCK_PIECE_COUNT,
    )
    assert isinstance(diagnostics.elapsed_time, float)


def test_solution_takes_an_immutable_snapshot_of_patterns() -> None:
    pattern = CutPattern("stock-10", {"demand-6": 1}, 2)
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
