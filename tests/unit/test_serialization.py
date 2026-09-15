import json
from pathlib import Path

import pytest

from saw import (
    Demand,
    Problem,
    SerializationError,
    SerializationErrorCode,
    Stock,
    problem_from_json,
)

REPOSITORY_ROOT = Path(__file__).parents[2]
VALID_EXAMPLE_PATH = REPOSITORY_ROOT / "examples" / "json" / "problem-v1-valid.json"

MINIMAL_VALID_JSON = json.dumps(
    {"format_version": 1, "stocks": [], "demands": []}
)


def _valid_stock(**overrides: object) -> dict[str, object]:
    record = {
        "stock_id": "stock-10",
        "material_id": "steel",
        "length": 10,
        "quantity": 2,
    }
    record.update(overrides)
    return record


def _valid_demand(**overrides: object) -> dict[str, object]:
    record = {
        "demand_id": "demand-6",
        "material_id": "steel",
        "length": 6,
        "quantity": 1,
    }
    record.update(overrides)
    return record


def _valid_payload(**overrides: object) -> dict[str, object]:
    payload = {
        "format_version": 1,
        "stocks": [_valid_stock()],
        "demands": [_valid_demand()],
    }
    payload.update(overrides)
    return payload


# --- Accepting every documented valid example ------------------------------


def test_parses_the_documented_valid_example_file() -> None:
    problem = problem_from_json(VALID_EXAMPLE_PATH.read_text(encoding="utf-8"))

    assert problem == Problem(
        stocks=(Stock(stock_id="stock-10", material_id="steel", length=10, quantity=2),),
        demands=(
            Demand(demand_id="demand-6", material_id="steel", length=6, quantity=1),
            Demand(demand_id="demand-4", material_id="steel", length=4, quantity=1),
        ),
    )


def test_parses_the_minimal_empty_problem() -> None:
    problem = problem_from_json(MINIMAL_VALID_JSON)

    assert problem == Problem(stocks=(), demands=())


def test_array_order_and_object_member_order_have_no_semantic_meaning() -> None:
    reordered = json.dumps(
        {
            "demands": [_valid_demand()],
            "stocks": [_valid_stock()],
            "format_version": 1,
        }
    )

    problem = problem_from_json(reordered)

    assert problem.stocks == (
        Stock(stock_id="stock-10", material_id="steel", length=10, quantity=2),
    )
    assert problem.demands == (
        Demand(demand_id="demand-6", material_id="steel", length=6, quantity=1),
    )


def test_whitespace_only_id_is_valid() -> None:
    payload = _valid_payload(stocks=[_valid_stock(stock_id="  ")])

    problem = problem_from_json(json.dumps(payload))

    assert problem.stocks[0].stock_id == "  "


def test_stock_and_demand_may_share_the_same_id() -> None:
    payload = _valid_payload(
        stocks=[_valid_stock(stock_id="shared")],
        demands=[_valid_demand(demand_id="shared")],
    )

    problem = problem_from_json(json.dumps(payload))

    assert problem.stocks[0].stock_id == "shared"
    assert problem.demands[0].demand_id == "shared"


# --- JSON syntax: invalid_json ----------------------------------------------


def test_syntactically_invalid_json_is_rejected() -> None:
    with pytest.raises(SerializationError) as excinfo:
        problem_from_json("{not valid json")

    assert excinfo.value.code is SerializationErrorCode.INVALID_JSON


def test_duplicate_top_level_member_names_are_rejected() -> None:
    duplicated = (
        '{"format_version": 1, "format_version": 1, "stocks": [], "demands": []}'
    )

    with pytest.raises(SerializationError) as excinfo:
        problem_from_json(duplicated)

    assert excinfo.value.code is SerializationErrorCode.INVALID_JSON


def test_duplicate_member_names_inside_a_stock_object_are_rejected() -> None:
    duplicated = json.dumps(_valid_payload()).replace(
        '"length": 10', '"length": 10, "length": 10'
    )

    with pytest.raises(SerializationError) as excinfo:
        problem_from_json(duplicated)

    assert excinfo.value.code is SerializationErrorCode.INVALID_JSON


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_non_standard_numeric_constants_are_rejected(constant: str) -> None:
    # json.dumps would quote the constant as a string, so the JSON text is
    # built by hand to keep it a bare (non-standard) numeric token, matching
    # what a producer that emits NaN/Infinity as a raw number would send.
    payload = _valid_payload()
    text = json.dumps(payload).replace('"length": 10', f'"length": {constant}', 1)

    with pytest.raises(SerializationError) as excinfo:
        problem_from_json(text)

    assert excinfo.value.code is SerializationErrorCode.INVALID_JSON


# --- Structural shape: schema_violation -------------------------------------


def test_unknown_top_level_field_is_rejected() -> None:
    payload = _valid_payload()
    payload["notes"] = "extension fields are not allowed"

    with pytest.raises(SerializationError) as excinfo:
        problem_from_json(json.dumps(payload))

    assert excinfo.value.code is SerializationErrorCode.SCHEMA_VIOLATION


def test_missing_top_level_field_is_rejected() -> None:
    payload = _valid_payload()
    del payload["demands"]

    with pytest.raises(SerializationError) as excinfo:
        problem_from_json(json.dumps(payload))

    assert excinfo.value.code is SerializationErrorCode.SCHEMA_VIOLATION


def test_non_object_top_level_payload_is_rejected() -> None:
    with pytest.raises(SerializationError) as excinfo:
        problem_from_json(json.dumps([1, 2, 3]))

    assert excinfo.value.code is SerializationErrorCode.SCHEMA_VIOLATION


def test_non_array_stocks_is_rejected() -> None:
    payload = _valid_payload(stocks={"stock-10": _valid_stock()})

    with pytest.raises(SerializationError) as excinfo:
        problem_from_json(json.dumps(payload))

    assert excinfo.value.code is SerializationErrorCode.SCHEMA_VIOLATION


def test_missing_stock_field_is_rejected() -> None:
    stock = _valid_stock()
    del stock["quantity"]
    payload = _valid_payload(stocks=[stock])

    with pytest.raises(SerializationError) as excinfo:
        problem_from_json(json.dumps(payload))

    assert excinfo.value.code is SerializationErrorCode.SCHEMA_VIOLATION
    assert excinfo.value.record == "stock"
    assert excinfo.value.record_index == 0


def test_unknown_demand_field_is_rejected() -> None:
    demand = _valid_demand()
    demand["notes"] = "extra"
    payload = _valid_payload(demands=[demand])

    with pytest.raises(SerializationError) as excinfo:
        problem_from_json(json.dumps(payload))

    assert excinfo.value.code is SerializationErrorCode.SCHEMA_VIOLATION
    assert excinfo.value.record == "demand"
    assert excinfo.value.record_index == 0


def test_non_object_array_element_is_rejected() -> None:
    payload = _valid_payload(stocks=["not-an-object"])

    with pytest.raises(SerializationError) as excinfo:
        problem_from_json(json.dumps(payload))

    assert excinfo.value.code is SerializationErrorCode.SCHEMA_VIOLATION
    assert excinfo.value.record_index == 0


@pytest.mark.parametrize(
    "field",
    ["stock_id", "material_id"],
)
def test_wrong_type_for_a_string_field_is_rejected(field: str) -> None:
    payload = _valid_payload(stocks=[_valid_stock(**{field: 123})])

    with pytest.raises(SerializationError) as excinfo:
        problem_from_json(json.dumps(payload))

    assert excinfo.value.code is SerializationErrorCode.SCHEMA_VIOLATION
    assert excinfo.value.field == field


@pytest.mark.parametrize("field", ["length", "quantity"])
def test_wrong_type_for_an_integer_field_is_rejected(field: str) -> None:
    payload = _valid_payload(stocks=[_valid_stock(**{field: "10"})])

    with pytest.raises(SerializationError) as excinfo:
        problem_from_json(json.dumps(payload))

    assert excinfo.value.code is SerializationErrorCode.SCHEMA_VIOLATION
    assert excinfo.value.field == field


def test_decimal_spelling_is_rejected_even_though_the_value_is_a_whole_number() -> None:
    # Mirrors the docs/formats.md invalid example: 6.0 is not integer spelling.
    payload = {
        "format_version": 1,
        "stocks": [],
        "demands": [_valid_demand(length=6.0)],
    }

    with pytest.raises(SerializationError) as excinfo:
        problem_from_json(json.dumps(payload))

    assert excinfo.value.code is SerializationErrorCode.SCHEMA_VIOLATION
    assert excinfo.value.field == "length"


def test_boolean_is_rejected_for_an_integer_field() -> None:
    payload = _valid_payload(stocks=[_valid_stock(quantity=True)])

    with pytest.raises(SerializationError) as excinfo:
        problem_from_json(json.dumps(payload))

    assert excinfo.value.code is SerializationErrorCode.SCHEMA_VIOLATION
    assert excinfo.value.field == "quantity"


# --- format_version handling -------------------------------------------


def test_missing_format_version_is_a_schema_violation() -> None:
    payload = _valid_payload()
    del payload["format_version"]

    with pytest.raises(SerializationError) as excinfo:
        problem_from_json(json.dumps(payload))

    assert excinfo.value.code is SerializationErrorCode.SCHEMA_VIOLATION


@pytest.mark.parametrize("value", ["1", 1.0, True])
def test_wrongly_spelled_format_version_is_a_schema_violation(value: object) -> None:
    payload = _valid_payload(format_version=value)

    with pytest.raises(SerializationError) as excinfo:
        problem_from_json(json.dumps(payload))

    assert excinfo.value.code is SerializationErrorCode.SCHEMA_VIOLATION
    assert excinfo.value.field == "format_version"


def test_a_well_formed_but_unsupported_format_version_is_rejected() -> None:
    payload = _valid_payload(format_version=2)

    with pytest.raises(SerializationError) as excinfo:
        problem_from_json(json.dumps(payload))

    assert excinfo.value.code is SerializationErrorCode.UNSUPPORTED_FORMAT_VERSION
    assert excinfo.value.field == "format_version"


# --- Semantic validation flows through the library validation path ---------


def test_duplicate_stock_ids_are_a_semantic_validation_failure() -> None:
    # Mirrors the docs/formats.md invalid example.
    payload = {
        "format_version": 1,
        "stocks": [
            {"stock_id": "stock-a", "material_id": "steel", "length": 10, "quantity": 1},
            {"stock_id": "stock-a", "material_id": "steel", "length": 20, "quantity": 1},
        ],
        "demands": [],
    }

    with pytest.raises(SerializationError) as excinfo:
        problem_from_json(json.dumps(payload))

    assert excinfo.value.code is SerializationErrorCode.SEMANTIC_VALIDATION


def test_empty_id_is_a_semantic_validation_failure() -> None:
    payload = _valid_payload(demands=[_valid_demand(demand_id="")])

    with pytest.raises(SerializationError) as excinfo:
        problem_from_json(json.dumps(payload))

    assert excinfo.value.code is SerializationErrorCode.SEMANTIC_VALIDATION


@pytest.mark.parametrize("field", ["length", "quantity"])
def test_non_positive_integer_is_a_semantic_validation_failure(field: str) -> None:
    payload = _valid_payload(stocks=[_valid_stock(**{field: 0})])

    with pytest.raises(SerializationError) as excinfo:
        problem_from_json(json.dumps(payload))

    assert excinfo.value.code is SerializationErrorCode.SEMANTIC_VALIDATION


def test_negative_length_is_a_semantic_validation_failure() -> None:
    payload = _valid_payload(demands=[_valid_demand(length=-6)])

    with pytest.raises(SerializationError) as excinfo:
        problem_from_json(json.dumps(payload))

    assert excinfo.value.code is SerializationErrorCode.SEMANTIC_VALIDATION


def test_a_non_empty_demand_array_with_an_empty_stock_array_is_valid() -> None:
    # Potential infeasibility is not a format error.
    payload = {
        "format_version": 1,
        "stocks": [],
        "demands": [_valid_demand()],
    }

    problem = problem_from_json(json.dumps(payload))

    assert problem.stocks == ()
    assert len(problem.demands) == 1
