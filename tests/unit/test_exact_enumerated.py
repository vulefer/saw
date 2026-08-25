import pulp

from saw import Demand, Problem, Stock
from saw.backends.exact_enumerated import (
    add_exact_demand_constraints,
    add_exact_stock_constraints,
    build_pattern_variables,
)
from saw.patterns import Pattern


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

    add_exact_stock_constraints(model, problem, variables)

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
    add_exact_stock_constraints(model, problem, variables)
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
    add_exact_stock_constraints(model, problem, variables)

    first_constraint = _constraint_by_name(model, "bounded_stock_000000")
    second_constraint = _constraint_by_name(model, "bounded_stock_000001")
    variables[first_pattern].varValue = 2
    variables[second_pattern].varValue = 2

    assert not first_constraint.valid(0)
    assert second_constraint.valid(0)
