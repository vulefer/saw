import pulp

from saw import Problem, Stock
from saw.backends.exact_enumerated import build_pattern_variables
from saw.patterns import Pattern


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
