from saw.exceptions import ValidationError
from saw.models import Demand, Problem, Stock


def validate_problem(problem: Problem) -> None:
    for index, stock in enumerate(problem.stocks):
        _validate_stock(stock, index)
    for index, demand in enumerate(problem.demands):
        _validate_demand(demand, index)


def _validate_stock(stock: Stock, index: int) -> None:
    record = f"stock[{index}] ({stock.stock_id!r})"
    _require_non_empty_string(stock.stock_id, record, "stock_id")
    _require_non_empty_string(stock.material_id, record, "material_id")
    _require_positive_integer(stock.length, record, "length")
    _require_positive_integer(stock.quantity, record, "quantity")


def _validate_demand(demand: Demand, index: int) -> None:
    record = f"demand[{index}] ({demand.demand_id!r})"
    _require_non_empty_string(demand.demand_id, record, "demand_id")
    _require_non_empty_string(demand.material_id, record, "material_id")
    _require_positive_integer(demand.length, record, "length")
    _require_positive_integer(demand.quantity, record, "quantity")


def _require_non_empty_string(value: object, record: str, field: str) -> None:
    if not isinstance(value, str) or value == "":
        raise ValidationError(
            f"{record}: {field} must be a non-empty string."
        )


def _require_positive_integer(value: object, record: str, field: str) -> None:
    # bool is a subclass of int, so it must be rejected explicitly.
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError(
            f"{record}: {field} must be a positive integer."
        )

