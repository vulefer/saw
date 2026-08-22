from collections.abc import Callable

import pytest

from saw import Demand, Problem, Stock, ValidationError, validate_problem


def test_valid_problem_passes_validation() -> None:
    problem = Problem(
        stocks=(Stock("stock-10", "steel", 10, 2),),
        demands=(Demand("demand-6", "steel", 6, 3),),
    )

    assert validate_problem(problem) is None


@pytest.mark.parametrize(
    ("make_stock", "field"),
    [
        (lambda: Stock("", "steel", 10, 2), "stock_id"),
        (lambda: Stock(1, "steel", 10, 2), "stock_id"),
        (lambda: Stock("stock-10", "", 10, 2), "material_id"),
        (lambda: Stock("stock-10", None, 10, 2), "material_id"),
        (lambda: Stock("stock-10", "steel", 0, 2), "length"),
        (lambda: Stock("stock-10", "steel", -1, 2), "length"),
        (lambda: Stock("stock-10", "steel", 1.5, 2), "length"),
        (lambda: Stock("stock-10", "steel", True, 2), "length"),
        (lambda: Stock("stock-10", "steel", 10, 0), "quantity"),
        (lambda: Stock("stock-10", "steel", 10, -1), "quantity"),
        (lambda: Stock("stock-10", "steel", 10, 1.5), "quantity"),
        (lambda: Stock("stock-10", "steel", 10, False), "quantity"),
    ],
)
def test_invalid_stock_primitive_identifies_record_and_field(
    make_stock: Callable[[], Stock],
    field: str,
) -> None:
    problem = Problem(stocks=(make_stock(),), demands=())

    with pytest.raises(ValidationError) as captured:
        validate_problem(problem)

    message = str(captured.value)
    assert message.startswith("stock[0]")
    assert field in message


@pytest.mark.parametrize(
    ("make_demand", "field"),
    [
        (lambda: Demand("", "steel", 6, 3), "demand_id"),
        (lambda: Demand(1, "steel", 6, 3), "demand_id"),
        (lambda: Demand("demand-6", "", 6, 3), "material_id"),
        (lambda: Demand("demand-6", None, 6, 3), "material_id"),
        (lambda: Demand("demand-6", "steel", 0, 3), "length"),
        (lambda: Demand("demand-6", "steel", -1, 3), "length"),
        (lambda: Demand("demand-6", "steel", 1.5, 3), "length"),
        (lambda: Demand("demand-6", "steel", True, 3), "length"),
        (lambda: Demand("demand-6", "steel", 6, 0), "quantity"),
        (lambda: Demand("demand-6", "steel", 6, -1), "quantity"),
        (lambda: Demand("demand-6", "steel", 6, 1.5), "quantity"),
        (lambda: Demand("demand-6", "steel", 6, False), "quantity"),
    ],
)
def test_invalid_demand_primitive_identifies_record_and_field(
    make_demand: Callable[[], Demand],
    field: str,
) -> None:
    problem = Problem(stocks=(), demands=(make_demand(),))

    with pytest.raises(ValidationError) as captured:
        validate_problem(problem)

    message = str(captured.value)
    assert message.startswith("demand[0]")
    assert field in message


def test_whitespace_identifier_is_not_normalized_or_rejected() -> None:
    problem = Problem(
        stocks=(Stock(" ", " ", 10, 1),),
        demands=(Demand(" ", " ", 6, 1),),
    )

    assert validate_problem(problem) is None


def test_duplicate_stock_ids_produce_validation_error() -> None:
    problem = Problem(
        stocks=(
            Stock("stock-10", "steel", 10, 1),
            Stock("stock-10", "steel", 20, 1),
        ),
        demands=(),
    )

    with pytest.raises(ValidationError) as captured:
        validate_problem(problem)

    message = str(captured.value)
    assert "stock" in message
    assert repr("stock-10") in message


def test_duplicate_demand_ids_produce_validation_error() -> None:
    problem = Problem(
        stocks=(),
        demands=(
            Demand("demand-6", "steel", 6, 1),
            Demand("demand-6", "steel", 4, 1),
        ),
    )

    with pytest.raises(ValidationError) as captured:
        validate_problem(problem)

    message = str(captured.value)
    assert "demand" in message
    assert repr("demand-6") in message


def test_stock_and_demand_ids_occupy_separate_namespaces() -> None:
    shared_id = "shared-id"
    problem = Problem(
        stocks=(Stock(shared_id, "steel", 10, 1),),
        demands=(Demand(shared_id, "steel", 6, 1),),
    )

    assert validate_problem(problem) is None


def test_duplicate_error_identifies_every_conflicting_id() -> None:
    problem = Problem(
        stocks=(
            Stock("stock-b", "steel", 10, 1),
            Stock("stock-a", "steel", 10, 1),
            Stock("stock-b", "steel", 20, 1),
            Stock("stock-a", "steel", 20, 1),
        ),
        demands=(),
    )

    with pytest.raises(ValidationError) as captured:
        validate_problem(problem)

    message = str(captured.value)
    assert repr("stock-a") in message
    assert repr("stock-b") in message
