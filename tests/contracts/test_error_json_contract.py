from copy import deepcopy
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
import pytest

REPOSITORY_ROOT = Path(__file__).parents[2]
SCHEMA_PATH = REPOSITORY_ROOT / "src" / "saw" / "schemas" / "error-v1.schema.json"
EXAMPLE_PATH = REPOSITORY_ROOT / "examples" / "json" / "error-v1-invalid-problem.json"

ERROR_CODES = {
    "invalid_cli_usage",
    "input_read_error",
    "invalid_json",
    "unsupported_format_version",
    "invalid_problem",
    "backend_unavailable",
    "internal_verification_error",
    "internal_error",
    "output_write_error",
}


@pytest.fixture(scope="module")
def schema() -> dict[str, object]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def validator(schema: dict[str, object]) -> Draft202012Validator:
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_schema_is_closed_and_requires_the_complete_envelope(
    schema: dict[str, object],
) -> None:
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"format_version", "error"}
    assert set(schema["properties"]) == {"format_version", "error"}
    assert schema["properties"]["format_version"] == {"const": 1}

    error_schema = schema["$defs"]["error"]
    assert error_schema["additionalProperties"] is False
    assert set(error_schema["required"]) == {"code", "message"}
    assert set(error_schema["properties"]) == {"code", "message"}


def test_schema_defines_the_stable_machine_readable_codes(
    schema: dict[str, object],
) -> None:
    assert set(schema["$defs"]["error"]["properties"]["code"]["enum"]) == ERROR_CODES


def test_example_is_valid(validator: Draft202012Validator) -> None:
    example = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))

    validator.validate(example)
    assert example["error"]["code"] == "invalid_problem"
    assert example["error"]["message"]


@pytest.mark.parametrize("code", sorted(ERROR_CODES))
def test_every_documented_error_code_is_valid(
    validator: Draft202012Validator,
    code: str,
) -> None:
    validator.validate(
        {
            "format_version": 1,
            "error": {"code": code, "message": "Diagnostic text."},
        }
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.update(extra=True),
        lambda payload: payload["error"].update(extra=True),
        lambda payload: payload["error"].pop("code"),
        lambda payload: payload["error"].pop("message"),
        lambda payload: payload["error"].update(code="infeasible"),
        lambda payload: payload["error"].update(message=""),
    ],
)
def test_unknown_missing_and_empty_members_are_rejected(
    validator: Draft202012Validator,
    mutation,
) -> None:
    payload = {
        "format_version": 1,
        "error": {"code": "internal_error", "message": "Failure."},
    }
    invalid_payload = deepcopy(payload)
    mutation(invalid_payload)

    with pytest.raises(ValidationError):
        validator.validate(invalid_payload)


def test_expected_solve_outcomes_are_not_error_codes() -> None:
    assert ERROR_CODES.isdisjoint({"optimal", "feasible", "infeasible", "limit", "unsupported"})
