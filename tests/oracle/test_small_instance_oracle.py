from pathlib import Path

from saw import Demand, Problem, Stock
from tests.oracle import OracleObjective, solve_small_instance


def test_empty_demand_is_feasible_without_using_stock() -> None:
    problem = Problem(
        stocks=(Stock("stock-10", "steel", 10, 2),),
        demands=(),
    )

    result = solve_small_instance(problem)

    assert result.feasible
    assert result.objective_value == OracleObjective(0, 0, 0)


def test_oracle_independently_finds_infeasible_problems() -> None:
    problems = (
        Problem(
            stocks=(),
            demands=(Demand("demand-1", "steel", 1, 1),),
        ),
        Problem(
            stocks=(Stock("stock-5", "steel", 5, 1),),
            demands=(Demand("demand-6", "steel", 6, 1),),
        ),
        Problem(
            stocks=(Stock("stock-10", "wood", 10, 1),),
            demands=(Demand("demand-5", "steel", 5, 1),),
        ),
    )

    for problem in problems:
        result = solve_small_instance(problem)

        assert not result.feasible
        assert result.objective_value is None


def test_primary_objective_minimizes_physical_stock_pieces() -> None:
    problem = Problem(
        stocks=(
            Stock("stock-10", "steel", 10, 1),
            Stock("stock-6", "steel", 6, 1),
            Stock("stock-4", "steel", 4, 1),
        ),
        demands=(
            Demand("demand-6", "steel", 6, 1),
            Demand("demand-4", "steel", 4, 1),
        ),
    )

    result = solve_small_instance(problem)

    assert result.objective_value == OracleObjective(1, 0, 1)


def test_secondary_objective_minimizes_leftover_across_stock_lengths() -> None:
    problem = Problem(
        stocks=(
            Stock("stock-12", "steel", 12, 1),
            Stock("stock-10", "steel", 10, 1),
        ),
        demands=(Demand("demand-6", "steel", 6, 1),),
    )

    result = solve_small_instance(problem)

    assert result.objective_value == OracleObjective(1, 4, 1)


def test_tertiary_objective_minimizes_distinct_positive_patterns() -> None:
    problem = Problem(
        stocks=(Stock("stock-11", "steel", 11, 2),),
        demands=(
            Demand("demand-a", "steel", 5, 2),
            Demand("demand-b", "steel", 5, 2),
        ),
    )

    result = solve_small_instance(problem)

    assert result.objective_value == OracleObjective(2, 2, 1)


def test_pattern_identity_includes_the_stock_record_id() -> None:
    problem = Problem(
        stocks=(
            Stock("stock-a", "steel", 5, 1),
            Stock("stock-b", "steel", 5, 1),
        ),
        demands=(Demand("demand-5", "steel", 5, 2),),
    )

    result = solve_small_instance(problem)

    assert result.objective_value == OracleObjective(2, 0, 2)


def test_bounded_inventory_is_enforced_per_stock_record() -> None:
    feasible_problem = Problem(
        stocks=(
            Stock("stock-8", "steel", 8, 1),
            Stock("stock-5", "steel", 5, 1),
        ),
        demands=(Demand("demand-5", "steel", 5, 2),),
    )
    infeasible_problem = Problem(
        stocks=(
            Stock("stock-8", "steel", 8, 1),
            Stock("stock-4", "steel", 4, 3),
        ),
        demands=(Demand("demand-5", "steel", 5, 2),),
    )

    feasible_result = solve_small_instance(feasible_problem)
    infeasible_result = solve_small_instance(infeasible_problem)

    assert feasible_result.feasible
    assert feasible_result.objective_value == OracleObjective(2, 3, 2)
    assert not infeasible_result.feasible
    assert infeasible_result.objective_value is None


def test_oracle_does_not_import_production_solver_components() -> None:
    source_file = __import__(
        "tests.oracle.small_instance_oracle",
        fromlist=["__file__"],
    ).__file__
    assert source_file is not None

    source = Path(source_file).read_text(encoding="utf-8")

    assert "saw.patterns" not in source
    assert "saw.backends" not in source
    assert "saw.verification" not in source
