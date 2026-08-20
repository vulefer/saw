from dataclasses import FrozenInstanceError, fields

import pytest

from saw import Demand, Problem, Stock


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
