import json
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]
SCHEMA_PATH = REPOSITORY_ROOT / "src" / "saw" / "schemas" / "problem-v1.schema.json"
VALID_EXAMPLE_PATH = REPOSITORY_ROOT / "examples" / "json" / "problem-v1-valid.json"


def test_schema_is_valid_json_and_closes_every_object() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["stock"]["additionalProperties"] is False
    assert schema["$defs"]["demand"]["additionalProperties"] is False


def test_schema_requires_the_complete_version_1_envelope() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert set(schema["required"]) == {
        "format_version",
        "stocks",
        "demands",
    }
    assert set(schema["properties"]) == {
        "format_version",
        "stocks",
        "demands",
    }
    assert schema["properties"]["format_version"] == {"const": 1}


def test_stock_and_demand_schemas_require_named_ids_and_values() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert set(schema["$defs"]["stock"]["required"]) == {
        "stock_id",
        "material_id",
        "length",
        "quantity",
    }
    assert set(schema["$defs"]["demand"]["required"]) == {
        "demand_id",
        "material_id",
        "length",
        "quantity",
    }
    assert schema["$defs"]["nonEmptyIdentifier"] == {
        "type": "string",
        "minLength": 1,
    }
    assert schema["$defs"]["positiveInteger"] == {
        "type": "integer",
        "minimum": 1,
    }


def test_valid_example_uses_explicit_ids_instead_of_array_positions() -> None:
    payload = json.loads(VALID_EXAMPLE_PATH.read_text(encoding="utf-8"))

    assert payload["format_version"] == 1
    assert "length_unit" not in payload
    assert {stock["stock_id"] for stock in payload["stocks"]} == {"stock-10"}
    assert {demand["demand_id"] for demand in payload["demands"]} == {
        "demand-6",
        "demand-4",
    }
    assert all(record["material_id"] == "steel" for record in payload["stocks"])
    assert all(record["material_id"] == "steel" for record in payload["demands"])
