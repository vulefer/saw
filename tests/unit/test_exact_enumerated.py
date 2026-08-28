import pulp
import pytest

from saw import CutPattern, Demand, Problem, Stock
from saw.backends.base import BackendOptions
from saw.backends.exact_enumerated import (
    PrimaryStockCountResult,
    SecondaryLeftoverResult,
    TertiaryDistinctPatternCountResult,
    add_exact_demand_constraints,
    add_bounded_stock_constraints,
    build_pattern_use_indicators,
    build_pattern_variables,
    build_primary_stock_count_model,
    build_total_leftover_expression,
    configure_secondary_leftover_stage,
    configure_tertiary_pattern_count_stage,
    extract_backend_output,
    extract_positive_cut_patterns,
    solve_primary_stock_count_stage,
    solve_secondary_leftover_stage,
    solve_tertiary_pattern_count_stage,
    translate_exact_backend_status,
)
from saw.backends.pulp_backend import PulpCbcAdapter
from saw.models import (
    BackendStatus,
    ObjectiveStage,
    ObjectiveValue,
    SolutionStatus,
    TerminationReason,
)
from saw.patterns import Pattern


class FakeStageAdapter:
    def __init__(
        self,
        status: BackendStatus = BackendStatus.OPTIMAL,
        solve_error: Exception | None = None,
    ) -> None:
        self.status = status
        self.solve_error = solve_error
        self.model: pulp.LpProblem | None = None
        self.options: BackendOptions | None = None

    def solve(
        self,
        model: pulp.LpProblem,
        options: BackendOptions,
    ) -> BackendStatus:
        self.model = model
        self.options = options
        if self.solve_error is not None:
            raise self.solve_error
        return self.status


@pytest.mark.parametrize(
    ("backend_status", "solution_status"),
    [
        (BackendStatus.OPTIMAL, SolutionStatus.OPTIMAL),
        (BackendStatus.FEASIBLE, SolutionStatus.FEASIBLE),
        (BackendStatus.INFEASIBLE, SolutionStatus.INFEASIBLE),
        (BackendStatus.LIMIT_REACHED, SolutionStatus.LIMIT),
        (BackendStatus.NOT_RUN, SolutionStatus.ERROR),
        (BackendStatus.UNBOUNDED, SolutionStatus.ERROR),
        (BackendStatus.NOT_SOLVED, SolutionStatus.ERROR),
        (BackendStatus.FAILED, SolutionStatus.ERROR),
        (BackendStatus.UNKNOWN, SolutionStatus.ERROR),
    ],
)
def test_exact_backend_statuses_are_translated_without_stronger_claims(
    backend_status: BackendStatus,
    solution_status: SolutionStatus,
) -> None:
    assert translate_exact_backend_status(backend_status) is solution_status


def test_unrecognized_exact_backend_status_is_an_error() -> None:
    assert translate_exact_backend_status(object()) is SolutionStatus.ERROR  # type: ignore[arg-type]


def _model_constraints(
    model: pulp.LpProblem,
) -> tuple[pulp.LpConstraint, ...]:
    if hasattr(model, "get_constraint_by_name"):
        return tuple(model.constraints())
    return tuple(model.constraints.values())


def _constraint_by_name(
    model: pulp.LpProblem,
    name: str,
) -> pulp.LpConstraint:
    if hasattr(model, "get_constraint_by_name"):
        return model.get_constraint_by_name(name)
    return model.constraints[name]


def test_builds_one_non_negative_integer_variable_per_pattern() -> None:
    problem = Problem(
        stocks=(Stock("stock-10", "steel", 10, 3),),
        demands=(),
    )
    patterns = (
        Pattern("stock-10", {"demand-6": 1}),
        Pattern("stock-10", {"demand-4": 1}),
    )

    variables = build_pattern_variables(problem, patterns)

    assert set(variables) == set(patterns)
    assert all(variable.lowBound == 0 for variable in variables.values())
    assert all(variable.cat == pulp.LpInteger for variable in variables.values())


def test_variable_names_are_deterministic_for_reordered_patterns() -> None:
    problem = Problem(
        stocks=(Stock("stock-10", "steel", 10, 3),),
        demands=(),
    )
    patterns = (
        Pattern("stock-10", {"demand-6": 1}),
        Pattern("stock-10", {"demand-4": 1}),
        Pattern("stock-10", {"demand-6": 1, "demand-4": 1}),
    )

    original = build_pattern_variables(problem, patterns)
    reordered = build_pattern_variables(problem, tuple(reversed(patterns)))

    assert {
        pattern: variable.name
        for pattern, variable in original.items()
    } == {
        pattern: variable.name
        for pattern, variable in reordered.items()
    }
    assert {variable.name for variable in original.values()} == {
        "pattern_usage_000000",
        "pattern_usage_000001",
        "pattern_usage_000002",
    }


def test_equivalent_canonical_patterns_share_one_variable() -> None:
    problem = Problem(
        stocks=(Stock("stock-10", "steel", 10, 3),),
        demands=(),
    )
    patterns = (
        Pattern("stock-10", {"demand-4": 1, "demand-6": 0}),
        Pattern("stock-10", {"demand-4": 1}),
    )

    variables = build_pattern_variables(problem, patterns)

    assert len(variables) == 1
    assert next(iter(variables)).piece_counts == {"demand-4": 1}


def test_variable_upper_bounds_use_their_stock_availability() -> None:
    problem = Problem(
        stocks=(
            Stock("limited-stock", "steel", 10, 2),
            Stock("larger-stock", "steel", 10, 7),
        ),
        demands=(),
    )
    limited_pattern = Pattern("limited-stock", {"demand-4": 1})
    larger_pattern = Pattern("larger-stock", {"demand-4": 1})

    variables = build_pattern_variables(
        problem,
        (larger_pattern, limited_pattern),
    )

    assert variables[limited_pattern].upBound == 2
    assert variables[larger_pattern].upBound == 7


def test_empty_pattern_collection_returns_empty_mapping() -> None:
    problem = Problem(
        stocks=(Stock("stock-10", "steel", 10, 3),),
        demands=(),
    )

    assert build_pattern_variables(problem, ()) == {}


def test_adds_one_equality_constraint_per_demand_id() -> None:
    problem = Problem(
        stocks=(Stock("stock-20", "steel", 20, 5),),
        demands=(
            Demand("demand-b", "steel", 6, 3),
            Demand("demand-a", "steel", 4, 2),
        ),
    )
    mixed_pattern = Pattern(
        "stock-20",
        {"demand-a": 2, "demand-b": 1},
    )
    demand_b_pattern = Pattern("stock-20", {"demand-b": 2})
    variables = build_pattern_variables(
        problem,
        (mixed_pattern, demand_b_pattern),
    )
    model = pulp.LpProblem("exact_demands")

    add_exact_demand_constraints(model, problem, variables)

    assert tuple(
        constraint.name
        for constraint in _model_constraints(model)
    ) == (
        "exact_demand_000000",
        "exact_demand_000001",
    )
    demand_b_constraint = _constraint_by_name(model, "exact_demand_000000")
    demand_a_constraint = _constraint_by_name(model, "exact_demand_000001")
    assert demand_b_constraint.sense == pulp.LpConstraintEQ
    assert demand_a_constraint.sense == pulp.LpConstraintEQ
    assert demand_b_constraint.get(variables[mixed_pattern], 0) == 1
    assert demand_b_constraint.get(variables[demand_b_pattern], 0) == 2
    assert demand_a_constraint.get(variables[mixed_pattern], 0) == 2
    assert demand_a_constraint.get(variables[demand_b_pattern], 0) == 0


def test_exact_demand_constraint_rejects_underproduction_and_surplus() -> None:
    problem = Problem(
        stocks=(Stock("stock-10", "steel", 10, 3),),
        demands=(Demand("demand-4", "steel", 4, 2),),
    )
    pattern = Pattern("stock-10", {"demand-4": 1})
    variables = build_pattern_variables(problem, (pattern,))
    model = pulp.LpProblem("exact_demand")
    add_exact_demand_constraints(model, problem, variables)
    constraint = _constraint_by_name(model, "exact_demand_000000")

    variables[pattern].varValue = 1
    assert not constraint.valid(0)

    variables[pattern].varValue = 2
    assert constraint.valid(0)

    variables[pattern].varValue = 3
    assert not constraint.valid(0)


def test_demand_absent_from_all_patterns_creates_a_contradiction() -> None:
    problem = Problem(
        stocks=(Stock("stock-10", "steel", 10, 2),),
        demands=(
            Demand("reachable", "steel", 4, 1),
            Demand("missing", "steel", 3, 1),
        ),
    )
    pattern = Pattern("stock-10", {"reachable": 1})
    variables = build_pattern_variables(problem, (pattern,))
    model = pulp.LpProblem("missing_demand")
    add_exact_demand_constraints(model, problem, variables)

    missing_constraint = _constraint_by_name(model, "exact_demand_000001")

    assert len(missing_constraint) == 0
    assert missing_constraint.sense == pulp.LpConstraintEQ
    assert missing_constraint.constant == -1
    assert not missing_constraint.valid(0)


def test_adds_one_inventory_constraint_per_stock_id() -> None:
    problem = Problem(
        stocks=(
            Stock("steel-stock", "steel", 10, 3),
            Stock("wood-stock", "wood", 10, 2),
        ),
        demands=(),
    )
    steel_pattern = Pattern("steel-stock", {"steel-demand": 1})
    wood_pattern = Pattern("wood-stock", {"wood-demand": 1})
    variables = build_pattern_variables(
        problem,
        (wood_pattern, steel_pattern),
    )
    model = pulp.LpProblem("bounded_stock")

    add_bounded_stock_constraints(model, problem, variables)

    assert tuple(
        constraint.name
        for constraint in _model_constraints(model)
    ) == (
        "bounded_stock_000000",
        "bounded_stock_000001",
    )
    steel_constraint = _constraint_by_name(model, "bounded_stock_000000")
    wood_constraint = _constraint_by_name(model, "bounded_stock_000001")
    assert steel_constraint.sense == pulp.LpConstraintLE
    assert wood_constraint.sense == pulp.LpConstraintLE
    assert steel_constraint.get(variables[steel_pattern], 0) == 1
    assert steel_constraint.get(variables[wood_pattern], 0) == 0
    assert wood_constraint.get(variables[steel_pattern], 0) == 0
    assert wood_constraint.get(variables[wood_pattern], 0) == 1


def test_stock_constraint_limits_usage_across_all_of_its_patterns() -> None:
    problem = Problem(
        stocks=(Stock("stock-10", "steel", 10, 3),),
        demands=(),
    )
    first_pattern = Pattern("stock-10", {"demand-6": 1})
    second_pattern = Pattern("stock-10", {"demand-4": 2})
    variables = build_pattern_variables(
        problem,
        (first_pattern, second_pattern),
    )
    model = pulp.LpProblem("shared_stock_limit")
    add_bounded_stock_constraints(model, problem, variables)
    constraint = _constraint_by_name(model, "bounded_stock_000000")

    variables[first_pattern].varValue = 1
    variables[second_pattern].varValue = 2
    assert constraint.valid(0)

    variables[first_pattern].varValue = 2
    variables[second_pattern].varValue = 2
    assert not constraint.valid(0)


def test_equal_length_stock_records_remain_independently_bounded() -> None:
    problem = Problem(
        stocks=(
            Stock("first-stock", "steel", 10, 1),
            Stock("second-stock", "steel", 10, 4),
        ),
        demands=(),
    )
    first_pattern = Pattern("first-stock", {"demand-4": 1})
    second_pattern = Pattern("second-stock", {"demand-4": 1})
    variables = build_pattern_variables(
        problem,
        (first_pattern, second_pattern),
    )
    model = pulp.LpProblem("independent_stock_limits")
    add_bounded_stock_constraints(model, problem, variables)

    first_constraint = _constraint_by_name(model, "bounded_stock_000000")
    second_constraint = _constraint_by_name(model, "bounded_stock_000001")
    variables[first_pattern].varValue = 2
    variables[second_pattern].varValue = 2

    assert not first_constraint.valid(0)
    assert second_constraint.valid(0)


def test_primary_model_is_a_minimization_problem_with_all_constraints() -> None:
    problem = Problem(
        stocks=(Stock("stock-10", "steel", 10, 3),),
        demands=(Demand("demand-4", "steel", 4, 2),),
    )
    pattern = Pattern("stock-10", {"demand-4": 2})

    model, variables, stock_count = build_primary_stock_count_model(
        problem,
        (pattern,),
    )

    assert model.name == "primary_stock_count"
    assert model.sense == pulp.LpMinimize
    assert model.objective is stock_count
    assert set(variables) == {pattern}
    assert tuple(
        constraint.name
        for constraint in _model_constraints(model)
    ) == (
        "exact_demand_000000",
        "bounded_stock_000000",
    )


def test_primary_objective_counts_each_physical_stock_piece() -> None:
    problem = Problem(
        stocks=(Stock("stock-10", "steel", 10, 4),),
        demands=(Demand("demand-4", "steel", 4, 3),),
    )
    single_piece_pattern = Pattern("stock-10", {"demand-4": 1})
    double_piece_pattern = Pattern("stock-10", {"demand-4": 2})

    model, variables, stock_count = build_primary_stock_count_model(
        problem,
        (single_piece_pattern, double_piece_pattern),
    )

    assert model.objective is stock_count
    assert stock_count.get(variables[single_piece_pattern], 0) == 1
    assert stock_count.get(variables[double_piece_pattern], 0) == 1

    variables[single_piece_pattern].varValue = 3
    variables[double_piece_pattern].varValue = 0
    assert pulp.value(stock_count) == 3


def test_returned_stock_count_can_be_fixed_for_a_later_stage() -> None:
    problem = Problem(
        stocks=(Stock("stock-10", "steel", 10, 4),),
        demands=(Demand("demand-4", "steel", 4, 3),),
    )
    single_piece_pattern = Pattern("stock-10", {"demand-4": 1})
    double_piece_pattern = Pattern("stock-10", {"demand-4": 2})
    model, variables, stock_count = build_primary_stock_count_model(
        problem,
        (single_piece_pattern, double_piece_pattern),
    )

    model.addConstraint(
        stock_count == 2,
        name="fixed_optimal_stock_count",
    )
    fixed_count = _constraint_by_name(
        model,
        "fixed_optimal_stock_count",
    )

    variables[single_piece_pattern].varValue = 1
    variables[double_piece_pattern].varValue = 1
    assert fixed_count.valid(0)

    variables[single_piece_pattern].varValue = 3
    variables[double_piece_pattern].varValue = 0
    assert not fixed_count.valid(0)


def test_primary_model_handles_an_empty_pattern_collection() -> None:
    problem = Problem(stocks=(), demands=())

    model, variables, stock_count = build_primary_stock_count_model(
        problem,
        (),
    )

    assert variables == {}
    assert model.objective is stock_count
    assert len(stock_count) == 0
    assert stock_count.constant == 0
    assert _model_constraints(model) == ()


def test_primary_stage_records_a_proven_optimal_stock_count() -> None:
    problem = Problem(
        stocks=(Stock("stock-10", "steel", 10, 3),),
        demands=(Demand("demand-4", "steel", 4, 2),),
    )
    pattern = Pattern("stock-10", {"demand-4": 1})
    model, variables, stock_count = build_primary_stock_count_model(
        problem,
        (pattern,),
    )
    variables[pattern].varValue = 2.0
    adapter = FakeStageAdapter()
    options = BackendOptions(time_limit_seconds=5)

    result = solve_primary_stock_count_stage(
        model,
        stock_count,
        adapter,
        options,
    )

    assert result == PrimaryStockCountResult(
        backend_status=BackendStatus.OPTIMAL,
        optimal_stock_count=2,
    )
    assert adapter.model is model
    assert adapter.options is options


@pytest.mark.parametrize(
    "backend_status",
    [
        BackendStatus.FEASIBLE,
        BackendStatus.INFEASIBLE,
        BackendStatus.NOT_SOLVED,
        BackendStatus.UNKNOWN,
    ],
)
def test_primary_stage_records_no_optimum_without_an_optimality_proof(
    backend_status: BackendStatus,
) -> None:
    model = pulp.LpProblem("primary_stock_count", pulp.LpMinimize)
    stock_count = pulp.LpAffineExpression()
    adapter = FakeStageAdapter(status=backend_status)

    result = solve_primary_stock_count_stage(
        model,
        stock_count,
        adapter,
        BackendOptions(),
    )

    assert result == PrimaryStockCountResult(
        backend_status=backend_status,
        optimal_stock_count=None,
    )


def test_primary_stage_does_not_translate_backend_failure_to_infeasibility(
) -> None:
    model = pulp.LpProblem("primary_stock_count", pulp.LpMinimize)
    stock_count = pulp.LpAffineExpression()
    solve_error = RuntimeError("backend failed")
    adapter = FakeStageAdapter(solve_error=solve_error)

    with pytest.raises(RuntimeError) as captured:
        solve_primary_stock_count_stage(
            model,
            stock_count,
            adapter,
            BackendOptions(),
        )

    assert captured.value is solve_error


def test_primary_stage_rejects_a_missing_optimal_objective_value() -> None:
    problem = Problem(
        stocks=(Stock("stock-10", "steel", 10, 1),),
        demands=(Demand("demand-4", "steel", 4, 1),),
    )
    pattern = Pattern("stock-10", {"demand-4": 1})
    model, _, stock_count = build_primary_stock_count_model(
        problem,
        (pattern,),
    )

    with pytest.raises(
        RuntimeError,
        match="optimal result without an objective value",
    ):
        solve_primary_stock_count_stage(
            model,
            stock_count,
            FakeStageAdapter(),
            BackendOptions(),
        )


def test_primary_stage_rejects_a_non_integral_optimal_stock_count() -> None:
    problem = Problem(
        stocks=(Stock("stock-10", "steel", 10, 2),),
        demands=(Demand("demand-4", "steel", 4, 1),),
    )
    pattern = Pattern("stock-10", {"demand-4": 1})
    model, variables, stock_count = build_primary_stock_count_model(
        problem,
        (pattern,),
    )
    variables[pattern].varValue = 1.5

    with pytest.raises(RuntimeError, match="was not integral"):
        solve_primary_stock_count_stage(
            model,
            stock_count,
            FakeStageAdapter(),
            BackendOptions(),
        )


def test_total_leftover_expression_uses_stock_and_cut_lengths() -> None:
    problem = Problem(
        stocks=(
            Stock("stock-10", "steel", 10, 5),
            Stock("stock-14", "steel", 14, 5),
        ),
        demands=(
            Demand("demand-4", "steel", 4, 5),
            Demand("demand-3", "steel", 3, 5),
        ),
    )
    tight_pattern = Pattern(
        "stock-10",
        {"demand-4": 1, "demand-3": 2},
    )
    roomy_pattern = Pattern(
        "stock-14",
        {"demand-4": 1, "demand-3": 1},
    )
    variables = build_pattern_variables(
        problem,
        (roomy_pattern, tight_pattern),
    )

    total_leftover = build_total_leftover_expression(problem, variables)

    assert total_leftover.get(variables[tight_pattern], 0) == 0
    assert total_leftover.get(variables[roomy_pattern], 0) == 7

    variables[tight_pattern].varValue = 2
    variables[roomy_pattern].varValue = 3
    assert pulp.value(total_leftover) == 21


def test_secondary_stage_fixes_stock_count_and_replaces_objective() -> None:
    problem = Problem(
        stocks=(Stock("stock-10", "steel", 10, 4),),
        demands=(Demand("demand-4", "steel", 4, 3),),
    )
    single_piece_pattern = Pattern("stock-10", {"demand-4": 1})
    double_piece_pattern = Pattern("stock-10", {"demand-4": 2})
    model, variables, stock_count = build_primary_stock_count_model(
        problem,
        (single_piece_pattern, double_piece_pattern),
    )

    total_leftover = configure_secondary_leftover_stage(
        model,
        problem,
        variables,
        stock_count,
        optimal_stock_count=2,
    )

    fixed_count = _constraint_by_name(
        model,
        "fixed_optimal_stock_count",
    )
    assert fixed_count.sense == pulp.LpConstraintEQ
    assert model.objective is total_leftover
    assert total_leftover.get(variables[single_piece_pattern], 0) == 6
    assert total_leftover.get(variables[double_piece_pattern], 0) == 2

    variables[single_piece_pattern].varValue = 1
    variables[double_piece_pattern].varValue = 1
    assert fixed_count.valid(0)

    variables[single_piece_pattern].varValue = 3
    variables[double_piece_pattern].varValue = 0
    assert not fixed_count.valid(0)


def test_secondary_stage_records_a_proven_optimal_leftover() -> None:
    problem = Problem(
        stocks=(Stock("stock-10", "steel", 10, 3),),
        demands=(Demand("demand-4", "steel", 4, 2),),
    )
    pattern = Pattern("stock-10", {"demand-4": 1})
    variables = build_pattern_variables(problem, (pattern,))
    total_leftover = build_total_leftover_expression(problem, variables)
    variables[pattern].varValue = 2.0
    model = pulp.LpProblem("secondary_total_leftover", pulp.LpMinimize)
    adapter = FakeStageAdapter()
    options = BackendOptions(time_limit_seconds=5)

    result = solve_secondary_leftover_stage(
        model,
        total_leftover,
        adapter,
        options,
    )

    assert result == SecondaryLeftoverResult(
        backend_status=BackendStatus.OPTIMAL,
        optimal_total_leftover=12,
    )
    assert adapter.model is model
    assert adapter.options is options


@pytest.mark.parametrize(
    "backend_status",
    [
        BackendStatus.FEASIBLE,
        BackendStatus.INFEASIBLE,
        BackendStatus.LIMIT_REACHED,
        BackendStatus.NOT_SOLVED,
        BackendStatus.UNKNOWN,
    ],
)
def test_secondary_stage_records_no_optimum_without_an_optimality_proof(
    backend_status: BackendStatus,
) -> None:
    model = pulp.LpProblem("secondary_total_leftover", pulp.LpMinimize)
    total_leftover = pulp.LpAffineExpression()

    result = solve_secondary_leftover_stage(
        model,
        total_leftover,
        FakeStageAdapter(status=backend_status),
        BackendOptions(),
    )

    assert result == SecondaryLeftoverResult(
        backend_status=backend_status,
        optimal_total_leftover=None,
    )


def test_secondary_stage_propagates_backend_failure() -> None:
    model = pulp.LpProblem("secondary_total_leftover", pulp.LpMinimize)
    total_leftover = pulp.LpAffineExpression()
    solve_error = RuntimeError("backend failed")

    with pytest.raises(RuntimeError) as captured:
        solve_secondary_leftover_stage(
            model,
            total_leftover,
            FakeStageAdapter(solve_error=solve_error),
            BackendOptions(),
        )

    assert captured.value is solve_error


def test_secondary_stage_rejects_a_missing_optimal_objective_value() -> None:
    problem = Problem(
        stocks=(Stock("stock-10", "steel", 10, 1),),
        demands=(Demand("demand-4", "steel", 4, 1),),
    )
    pattern = Pattern("stock-10", {"demand-4": 1})
    variables = build_pattern_variables(problem, (pattern,))
    total_leftover = build_total_leftover_expression(problem, variables)

    with pytest.raises(
        RuntimeError,
        match="optimal result without an objective value",
    ):
        solve_secondary_leftover_stage(
            pulp.LpProblem("secondary_total_leftover", pulp.LpMinimize),
            total_leftover,
            FakeStageAdapter(),
            BackendOptions(),
        )


def test_secondary_stage_rejects_a_non_integral_optimal_leftover() -> None:
    problem = Problem(
        stocks=(Stock("stock-10", "steel", 10, 1),),
        demands=(Demand("demand-4", "steel", 4, 1),),
    )
    pattern = Pattern("stock-10", {"demand-4": 1})
    variables = build_pattern_variables(problem, (pattern,))
    total_leftover = build_total_leftover_expression(problem, variables)
    variables[pattern].varValue = 0.25

    with pytest.raises(RuntimeError, match="was not integral"):
        solve_secondary_leftover_stage(
            pulp.LpProblem("secondary_total_leftover", pulp.LpMinimize),
            total_leftover,
            FakeStageAdapter(),
            BackendOptions(),
        )


def test_secondary_optimization_preserves_primary_optimum() -> None:
    problem = Problem(
        stocks=(
            Stock("stock-10", "steel", 10, 1),
            Stock("stock-8", "steel", 8, 1),
        ),
        demands=(Demand("demand-6", "steel", 6, 1),),
    )
    long_stock_pattern = Pattern("stock-10", {"demand-6": 1})
    short_stock_pattern = Pattern("stock-8", {"demand-6": 1})
    model, variables, stock_count = build_primary_stock_count_model(
        problem,
        (long_stock_pattern, short_stock_pattern),
    )
    adapter = PulpCbcAdapter()
    options = BackendOptions()

    primary_result = solve_primary_stock_count_stage(
        model,
        stock_count,
        adapter,
        options,
    )
    assert primary_result == PrimaryStockCountResult(
        backend_status=BackendStatus.OPTIMAL,
        optimal_stock_count=1,
    )
    assert primary_result.optimal_stock_count is not None

    total_leftover = configure_secondary_leftover_stage(
        model,
        problem,
        variables,
        stock_count,
        optimal_stock_count=primary_result.optimal_stock_count,
    )
    secondary_result = solve_secondary_leftover_stage(
        model,
        total_leftover,
        adapter,
        options,
    )

    assert secondary_result == SecondaryLeftoverResult(
        backend_status=BackendStatus.OPTIMAL,
        optimal_total_leftover=2,
    )
    assert pulp.value(stock_count) == 1
    assert variables[short_stock_pattern].varValue == 1
    assert variables[long_stock_pattern].varValue == 0


def test_builds_one_binary_use_indicator_per_pattern() -> None:
    problem = Problem(
        stocks=(Stock("stock-10", "steel", 10, 4),),
        demands=(),
    )
    patterns = (
        Pattern("stock-10", {"demand-4": 1}),
        Pattern("stock-10", {"demand-6": 1}),
    )
    pattern_variables = build_pattern_variables(problem, patterns)
    model = pulp.LpProblem("pattern_use_indicators")

    indicators = build_pattern_use_indicators(
        model,
        problem,
        pattern_variables,
    )

    assert set(indicators) == set(pattern_variables)
    assert all(indicator.isBinary() for indicator in indicators.values())
    assert {
        indicator.name
        for indicator in indicators.values()
    } == {
        "pattern_used_000000",
        "pattern_used_000001",
    }


def test_pattern_use_indicators_are_linked_by_stock_availability() -> None:
    problem = Problem(
        stocks=(
            Stock("limited-stock", "steel", 10, 2),
            Stock("larger-stock", "steel", 10, 7),
        ),
        demands=(),
    )
    patterns = (
        Pattern("limited-stock", {"demand-4": 1}),
        Pattern("larger-stock", {"demand-4": 1}),
    )
    pattern_variables = build_pattern_variables(problem, patterns)
    model = pulp.LpProblem("linked_pattern_use")
    indicators = build_pattern_use_indicators(
        model,
        problem,
        pattern_variables,
    )
    stocks_by_id = {
        stock.stock_id: stock
        for stock in problem.stocks
    }

    for index, (pattern, indicator) in enumerate(indicators.items()):
        pattern_variable = pattern_variables[pattern]
        maximum_usage = stocks_by_id[pattern.stock_id].quantity
        upper_link = _constraint_by_name(
            model,
            f"link_pattern_use_upper_{index:06d}",
        )
        lower_link = _constraint_by_name(
            model,
            f"link_pattern_use_lower_{index:06d}",
        )

        assert upper_link.sense == pulp.LpConstraintLE
        assert upper_link.get(pattern_variable, 0) == 1
        assert upper_link.get(indicator, 0) == -maximum_usage
        assert lower_link.sense == pulp.LpConstraintLE
        assert lower_link.get(indicator, 0) == 1
        assert lower_link.get(pattern_variable, 0) == -1

        pattern_variable.varValue = 0
        indicator.varValue = 0
        assert upper_link.valid(0)
        assert lower_link.valid(0)

        pattern_variable.varValue = 1
        indicator.varValue = 0
        assert not upper_link.valid(0)

        pattern_variable.varValue = 0
        indicator.varValue = 1
        assert not lower_link.valid(0)

        pattern_variable.varValue = maximum_usage
        indicator.varValue = 1
        assert upper_link.valid(0)
        assert lower_link.valid(0)


def test_empty_pattern_collection_builds_no_use_indicators() -> None:
    problem = Problem(stocks=(), demands=())
    model = pulp.LpProblem("empty_pattern_use")

    indicators = build_pattern_use_indicators(
        model,
        problem,
        {},
    )

    assert indicators == {}
    assert _model_constraints(model) == ()


def test_distinct_pattern_objective_counts_only_positive_use() -> None:
    problem = Problem(
        stocks=(Stock("stock-10", "steel", 10, 5),),
        demands=(),
    )
    used_pattern = Pattern("stock-10", {"demand-4": 1})
    unused_pattern = Pattern("stock-10", {"demand-5": 1})
    pattern_variables = build_pattern_variables(
        problem,
        (used_pattern, unused_pattern),
    )
    model = pulp.LpProblem("positive_pattern_use")
    indicators = build_pattern_use_indicators(
        model,
        problem,
        pattern_variables,
    )
    distinct_pattern_count = pulp.lpSum(indicators.values())

    pattern_variables[used_pattern].varValue = 4
    indicators[used_pattern].varValue = 1
    pattern_variables[unused_pattern].varValue = 0
    indicators[unused_pattern].varValue = 0

    assert pulp.value(distinct_pattern_count) == 1
    assert all(
        constraint.valid(0)
        for constraint in _model_constraints(model)
    )


def test_tertiary_stage_fixes_leftover_and_replaces_objective() -> None:
    problem = Problem(
        stocks=(Stock("stock-10", "steel", 10, 4),),
        demands=(Demand("demand-4", "steel", 4, 3),),
    )
    single_piece_pattern = Pattern("stock-10", {"demand-4": 1})
    double_piece_pattern = Pattern("stock-10", {"demand-4": 2})
    model, pattern_variables, stock_count = build_primary_stock_count_model(
        problem,
        (single_piece_pattern, double_piece_pattern),
    )
    total_leftover = configure_secondary_leftover_stage(
        model,
        problem,
        pattern_variables,
        stock_count,
        optimal_stock_count=2,
    )

    distinct_pattern_count = configure_tertiary_pattern_count_stage(
        model,
        problem,
        pattern_variables,
        total_leftover,
        optimal_total_leftover=8,
    )

    fixed_stock_count = _constraint_by_name(
        model,
        "fixed_optimal_stock_count",
    )
    fixed_leftover = _constraint_by_name(
        model,
        "fixed_optimal_total_leftover",
    )
    assert fixed_stock_count.sense == pulp.LpConstraintEQ
    assert fixed_leftover.sense == pulp.LpConstraintEQ
    assert model.objective is distinct_pattern_count
    assert len(distinct_pattern_count) == 2
    assert all(
        variable.isBinary()
        for variable in distinct_pattern_count.keys()
    )
    assert all(
        coefficient == 1
        for coefficient in distinct_pattern_count.values()
    )

    pattern_variables[single_piece_pattern].varValue = 1
    pattern_variables[double_piece_pattern].varValue = 1
    assert fixed_stock_count.valid(0)
    assert fixed_leftover.valid(0)

    pattern_variables[single_piece_pattern].varValue = 0
    pattern_variables[double_piece_pattern].varValue = 2
    assert fixed_stock_count.valid(0)
    assert not fixed_leftover.valid(0)


def test_tertiary_stage_records_a_proven_optimal_pattern_count() -> None:
    model = pulp.LpProblem("tertiary_pattern_count", pulp.LpMinimize)
    first_indicator = pulp.LpVariable("first_used", cat=pulp.LpBinary)
    second_indicator = pulp.LpVariable("second_used", cat=pulp.LpBinary)
    distinct_pattern_count = pulp.lpSum(
        (first_indicator, second_indicator)
    )
    first_indicator.varValue = 1
    second_indicator.varValue = 0
    adapter = FakeStageAdapter()
    options = BackendOptions(time_limit_seconds=5)

    result = solve_tertiary_pattern_count_stage(
        model,
        distinct_pattern_count,
        adapter,
        options,
    )

    assert result == TertiaryDistinctPatternCountResult(
        backend_status=BackendStatus.OPTIMAL,
        optimal_distinct_pattern_count=1,
    )
    assert adapter.model is model
    assert adapter.options is options


@pytest.mark.parametrize(
    "backend_status",
    [
        BackendStatus.FEASIBLE,
        BackendStatus.INFEASIBLE,
        BackendStatus.LIMIT_REACHED,
        BackendStatus.NOT_SOLVED,
        BackendStatus.UNKNOWN,
    ],
)
def test_tertiary_stage_records_no_optimum_without_an_optimality_proof(
    backend_status: BackendStatus,
) -> None:
    model = pulp.LpProblem("tertiary_pattern_count", pulp.LpMinimize)
    distinct_pattern_count = pulp.LpAffineExpression()

    result = solve_tertiary_pattern_count_stage(
        model,
        distinct_pattern_count,
        FakeStageAdapter(status=backend_status),
        BackendOptions(),
    )

    assert result == TertiaryDistinctPatternCountResult(
        backend_status=backend_status,
        optimal_distinct_pattern_count=None,
    )


def test_tertiary_stage_propagates_backend_failure() -> None:
    model = pulp.LpProblem("tertiary_pattern_count", pulp.LpMinimize)
    distinct_pattern_count = pulp.LpAffineExpression()
    solve_error = RuntimeError("backend failed")

    with pytest.raises(RuntimeError) as captured:
        solve_tertiary_pattern_count_stage(
            model,
            distinct_pattern_count,
            FakeStageAdapter(solve_error=solve_error),
            BackendOptions(),
        )

    assert captured.value is solve_error


def test_tertiary_stage_rejects_a_missing_optimal_objective_value() -> None:
    model = pulp.LpProblem("tertiary_pattern_count", pulp.LpMinimize)
    indicator = pulp.LpVariable("pattern_used", cat=pulp.LpBinary)
    distinct_pattern_count = pulp.lpSum((indicator,))

    with pytest.raises(
        RuntimeError,
        match="optimal result without an objective value",
    ):
        solve_tertiary_pattern_count_stage(
            model,
            distinct_pattern_count,
            FakeStageAdapter(),
            BackendOptions(),
        )


def test_tertiary_stage_rejects_a_non_integral_pattern_count() -> None:
    model = pulp.LpProblem("tertiary_pattern_count", pulp.LpMinimize)
    indicator = pulp.LpVariable("pattern_used", cat=pulp.LpBinary)
    distinct_pattern_count = pulp.lpSum((indicator,))
    indicator.varValue = 0.5

    with pytest.raises(RuntimeError, match="was not integral"):
        solve_tertiary_pattern_count_stage(
            model,
            distinct_pattern_count,
            FakeStageAdapter(),
            BackendOptions(),
        )


def test_tertiary_optimization_preserves_both_earlier_optima() -> None:
    problem = Problem(
        stocks=(Stock("stock-11", "steel", 11, 2),),
        demands=(
            Demand("demand-a", "steel", 5, 2),
            Demand("demand-b", "steel", 5, 2),
        ),
    )
    mixed_pattern = Pattern(
        "stock-11",
        {"demand-a": 1, "demand-b": 1},
    )
    demand_a_pattern = Pattern("stock-11", {"demand-a": 2})
    demand_b_pattern = Pattern("stock-11", {"demand-b": 2})
    model, pattern_variables, stock_count = build_primary_stock_count_model(
        problem,
        (mixed_pattern, demand_a_pattern, demand_b_pattern),
    )
    adapter = PulpCbcAdapter()
    options = BackendOptions()

    primary_result = solve_primary_stock_count_stage(
        model,
        stock_count,
        adapter,
        options,
    )
    assert primary_result.optimal_stock_count == 2

    total_leftover = configure_secondary_leftover_stage(
        model,
        problem,
        pattern_variables,
        stock_count,
        optimal_stock_count=2,
    )
    secondary_result = solve_secondary_leftover_stage(
        model,
        total_leftover,
        adapter,
        options,
    )
    assert secondary_result.optimal_total_leftover == 2

    distinct_pattern_count = configure_tertiary_pattern_count_stage(
        model,
        problem,
        pattern_variables,
        total_leftover,
        optimal_total_leftover=2,
    )
    tertiary_result = solve_tertiary_pattern_count_stage(
        model,
        distinct_pattern_count,
        adapter,
        options,
    )

    assert tertiary_result == TertiaryDistinctPatternCountResult(
        backend_status=BackendStatus.OPTIMAL,
        optimal_distinct_pattern_count=1,
    )
    assert pulp.value(stock_count) == 2
    assert pulp.value(total_leftover) == 2
    assert pattern_variables[mixed_pattern].varValue == 2
    assert pattern_variables[demand_a_pattern].varValue == 0
    assert pattern_variables[demand_b_pattern].varValue == 0

    backend_result = extract_backend_output(
        pattern_variables,
        primary_result,
        secondary_result,
        tertiary_result,
        elapsed_time=0.25,
    )

    assert backend_result.candidate is not None
    assert backend_result.candidate.cut_patterns == (
        CutPattern(pattern=mixed_pattern, repetitions=2),
    )
    assert backend_result.candidate.objective_value == ObjectiveValue(
        stock_piece_count=2,
        total_leftover=2,
        distinct_pattern_count=1,
    )


def test_extracts_only_positive_patterns_using_stable_ids() -> None:
    problem = Problem(
        stocks=(Stock("stable-stock-id", "steel", 12, 5),),
        demands=(
            Demand("stable-demand-a", "steel", 4, 3),
            Demand("stable-demand-b", "steel", 3, 6),
        ),
    )
    used_pattern = Pattern(
        "stable-stock-id",
        {"stable-demand-a": 1, "stable-demand-b": 2},
    )
    unused_pattern = Pattern(
        "stable-stock-id",
        {"stable-demand-a": 2},
    )
    pattern_variables = build_pattern_variables(
        problem,
        (unused_pattern, used_pattern),
    )
    pattern_variables[used_pattern].varValue = 3.0
    pattern_variables[unused_pattern].varValue = 0.0

    cut_patterns = extract_positive_cut_patterns(pattern_variables)

    assert len(cut_patterns) == 1
    assert cut_patterns[0].stock_id == "stable-stock-id"
    assert cut_patterns[0].piece_counts == {
        "stable-demand-a": 1,
        "stable-demand-b": 2,
    }
    assert cut_patterns[0].repetitions == 3
    assert isinstance(cut_patterns[0].repetitions, int)


@pytest.mark.parametrize(
    ("raw_repetitions", "message"),
    [
        (None, "returned no value"),
        (1.5, "was not integral"),
        (-1.0, "was negative"),
    ],
)
def test_rejects_invalid_pattern_repetitions(
    raw_repetitions: float | None,
    message: str,
) -> None:
    problem = Problem(
        stocks=(Stock("stock-10", "steel", 10, 2),),
        demands=(Demand("demand-4", "steel", 4, 1),),
    )
    pattern = Pattern("stock-10", {"demand-4": 1})
    pattern_variables = build_pattern_variables(problem, (pattern,))
    pattern_variables[pattern].varValue = raw_repetitions

    with pytest.raises(RuntimeError, match=message):
        extract_positive_cut_patterns(pattern_variables)


def test_backend_output_records_objectives_and_diagnostics() -> None:
    result = extract_backend_output(
        {},
        PrimaryStockCountResult(BackendStatus.OPTIMAL, 0),
        SecondaryLeftoverResult(BackendStatus.OPTIMAL, 0),
        TertiaryDistinctPatternCountResult(BackendStatus.OPTIMAL, 0),
        elapsed_time=1.25,
    )

    assert result.candidate is not None
    assert result.candidate.cut_patterns == ()
    assert result.candidate.objective_value == ObjectiveValue(0, 0, 0)
    assert result.diagnostics.termination_reason is TerminationReason.COMPLETED
    assert result.diagnostics.backend_status is BackendStatus.OPTIMAL
    assert result.diagnostics.elapsed_time == 1.25
    assert result.diagnostics.patterns_generated == 0
    assert result.diagnostics.iterations == 1
    assert result.diagnostics.completed_objective_stages == (
        ObjectiveStage.STOCK_PIECE_COUNT,
        ObjectiveStage.TOTAL_LEFTOVER,
        ObjectiveStage.DISTINCT_PATTERN_COUNT,
    )
    assert result.diagnostics.lower_bound == 0
    assert result.diagnostics.upper_bound == 0


@pytest.mark.parametrize(
    ("primary_result", "secondary_result", "tertiary_result", "stage"),
    [
        (
            PrimaryStockCountResult(BackendStatus.NOT_SOLVED, None),
            SecondaryLeftoverResult(BackendStatus.OPTIMAL, 0),
            TertiaryDistinctPatternCountResult(BackendStatus.OPTIMAL, 0),
            ObjectiveStage.STOCK_PIECE_COUNT,
        ),
        (
            PrimaryStockCountResult(BackendStatus.OPTIMAL, 0),
            SecondaryLeftoverResult(BackendStatus.LIMIT_REACHED, None),
            TertiaryDistinctPatternCountResult(BackendStatus.OPTIMAL, 0),
            ObjectiveStage.TOTAL_LEFTOVER,
        ),
        (
            PrimaryStockCountResult(BackendStatus.OPTIMAL, 0),
            SecondaryLeftoverResult(BackendStatus.OPTIMAL, 0),
            TertiaryDistinctPatternCountResult(BackendStatus.FEASIBLE, None),
            ObjectiveStage.DISTINCT_PATTERN_COUNT,
        ),
    ],
)
def test_backend_output_requires_all_three_proven_optima(
    primary_result: PrimaryStockCountResult,
    secondary_result: SecondaryLeftoverResult,
    tertiary_result: TertiaryDistinctPatternCountResult,
    stage: ObjectiveStage,
) -> None:
    with pytest.raises(RuntimeError, match=stage.value):
        extract_backend_output(
            {},
            primary_result,
            secondary_result,
            tertiary_result,
            elapsed_time=0.0,
        )
