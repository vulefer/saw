from saw.exceptions import ValidationError, ValidationErrorCode
from saw.models import Demand, Problem, Stock


def validate_problem(problem: Problem) -> None:
    # problem validation "parent" function
    validate_problem_records(problem)
    validate_problem_collections(problem)


def validate_problem_records(problem: Problem) -> None:
    # validation of the individual records
    for index, stock in enumerate(problem.stocks):
        validate_stock(stock, index)
    for index, demand in enumerate(problem.demands):
        validate_demand(demand, index)


def validate_problem_collections(problem: Problem) -> None:
    # validation of the whole collections and structural rules
    require_unique_ids(tuple(stock.stock_id for stock in problem.stocks), "stock")
    require_unique_ids(tuple(demand.demand_id for demand in problem.demands), "demand")


def validate_stock(stock: Stock, index: int) -> None:
    record = f"stock[{index}] ({stock.stock_id!r})"
    require_non_empty_string(
        stock.stock_id,
        record,
        "stock_id",
        ValidationErrorCode.INVALID_STOCK_ID,
        "stock",
        index,
    )
    require_non_empty_string(
        stock.material_id,
        record,
        "material_id",
        ValidationErrorCode.INVALID_STOCK_MATERIAL_ID,
        "stock",
        index,
    )
    require_positive_integer(
        stock.length,
        record,
        "length",
        ValidationErrorCode.INVALID_STOCK_LENGTH,
        "stock",
        index,
    )
    require_positive_integer(
        stock.quantity,
        record,
        "quantity",
        ValidationErrorCode.INVALID_STOCK_QUANTITY,
        "stock",
        index,
    )


def validate_demand(demand: Demand, index: int) -> None:
    record = f"demand[{index}] ({demand.demand_id!r})"
    require_non_empty_string(
        demand.demand_id,
        record,
        "demand_id",
        ValidationErrorCode.INVALID_DEMAND_ID,
        "demand",
        index,
    )
    require_non_empty_string(
        demand.material_id,
        record,
        "material_id",
        ValidationErrorCode.INVALID_DEMAND_MATERIAL_ID,
        "demand",
        index,
    )
    require_positive_integer(
        demand.length,
        record,
        "length",
        ValidationErrorCode.INVALID_DEMAND_LENGTH,
        "demand",
        index,
    )
    require_positive_integer(
        demand.quantity,
        record,
        "quantity",
        ValidationErrorCode.INVALID_DEMAND_QUANTITY,
        "demand",
        index,
    )


def require_non_empty_string(
    value: object,
    record: str,
    field: str,
    code: ValidationErrorCode,
    record_kind: str,
    record_index: int,
) -> None:
    if not isinstance(value, str) or value == "":
        raise ValidationError(
            f"{record}: {field} must be a non-empty string.",
            code=code,
            record=record_kind,
            record_index=record_index,
            field=field,
        )


def require_positive_integer(
    value: object,
    record: str,
    field: str,
    code: ValidationErrorCode,
    record_kind: str,
    record_index: int,
) -> None:
    # bool is a subclass of int, so it must be rejected explicitly.
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError(
            f"{record}: {field} must be a positive integer.",
            code=code,
            record=record_kind,
            record_index=record_index,
            field=field,
        )


# checks if all of the ids in the tuple are unique
def require_unique_ids(identifiers: tuple[str, ...], field: str) -> None:
    duplicates = find_duplicates(identifiers)

    if duplicates:
        sorted_duplicates = tuple(sorted(duplicates))
        conflicting_ids = ", ".join(repr(value) for value in sorted_duplicates)
        code = (
            ValidationErrorCode.DUPLICATE_STOCK_ID
            if field == "stock"
            else ValidationErrorCode.DUPLICATE_DEMAND_ID
        )
        raise ValidationError(
            f"Duplicate {field} IDs: {conflicting_ids}.",
            code=code,
            record=field,
            field=f"{field}_id",
            conflicting_ids=sorted_duplicates,
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
