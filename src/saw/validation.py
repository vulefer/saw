from saw.exceptions import ValidationError
from saw.models import Demand, Problem, Stock


def validate_problem(problem: Problem) -> None:
    # validating the individual stocks and demands
    for index, stock in enumerate(problem.stocks):
        validate_stock(stock, index)
    for index, demand in enumerate(problem.demands):
        validate_demand(demand, index)

    # validating the stocks and demands collections
    require_unique_ids(tuple(stock.stock_id for stock in problem.stocks), "stock")
    require_unique_ids(tuple(demand.demand_id for demand in problem.demands), "demand")

def validate_stock(stock: Stock, index: int) -> None:
    record = f"stock[{index}] ({stock.stock_id!r})"
    require_non_empty_string(stock.stock_id, record, "stock_id")
    require_non_empty_string(stock.material_id, record, "material_id")
    require_positive_integer(stock.length, record, "length")
    require_positive_integer(stock.quantity, record, "quantity")


def validate_demand(demand: Demand, index: int) -> None:
    record = f"demand[{index}] ({demand.demand_id!r})"
    require_non_empty_string(demand.demand_id, record, "demand_id")
    require_non_empty_string(demand.material_id, record, "material_id")
    require_positive_integer(demand.length, record, "length")
    require_positive_integer(demand.quantity, record, "quantity")


def require_non_empty_string(value: object, record: str, field: str) -> None:
    if not isinstance(value, str) or value == "":
        raise ValidationError(
            f"{record}: {field} must be a non-empty string."
        )


def require_positive_integer(value: object, record: str, field: str) -> None:
    # bool is a subclass of int, so it must be rejected explicitly.
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError(
            f"{record}: {field} must be a positive integer."
        )

# checks if all of the ids in the tuple are unique
def require_unique_ids(identifiers: tuple[str, ...], field: str) -> None:
    duplicates = find_duplicates(identifiers)

    if duplicates:
        conflicting_ids = ", ".join(repr(value) for value in sorted(duplicates))
        raise ValidationError(
            f"Duplicate {field} IDs: {conflicting_ids}."
        )

def find_duplicates(records: tuple[str, ...]) -> set[str]:
    seen = set()
    duplicates = set()

    for record in records:
        identifier = record
        if identifier in seen:
            duplicates.add(identifier)
        else:
            seen.add(identifier)

    return duplicates