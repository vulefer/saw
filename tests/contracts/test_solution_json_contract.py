from copy import deepcopy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


REPOSITORY_ROOT = Path(__file__).parents[2]
SCHEMA_PATH = REPOSITORY_ROOT / "schemas" / "solution-v1.schema.json"
OPTIMAL_EXAMPLE_PATH = (
    REPOSITORY_ROOT / "examples" / "json" / "solution-v1-optimal.json"
)


@pytest.fixture(scope="module")
def schema() -> dict[str, object]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def validator(schema: dict[str, object]) -> Draft202012Validator:
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _diagnostics(
    termination_reason: str,
    backend_status: str,
    *,
    completed_objective_stages: list[str] | None = None,
    lower_bound: int | None = None,
    upper_bound: int | None = None,
) -> dict[str, object]:
    return {
        "termination_reason": termination_reason,
        "backend_status": backend_status,
        "elapsed_time": 0.25,
        "patterns_generated": 4,
        "iterations": 2,
        "completed_objective_stages": completed_objective_stages or [],
        "lower_bound": lower_bound,
        "upper_bound": upper_bound,
    }


def _verified_plan_fields() -> dict[str, object]:
    return {
        "cut_patterns": [
            {
                "stock_id": "stock-10",
                "piece_counts": {"demand-4": 1, "demand-6": 1},
                "repetitions": 2,
            }
        ],
        "objective_value": {
            "stock_piece_count": 2,
            "total_leftover": 0,
            "distinct_pattern_count": 1,
        },
        "verification_report": {"result": "valid", "findings": []},
    }


def _payload(
    solution_status: str,
    diagnostics: dict[str, object],
    *,
    with_plan: bool = False,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "format_version": 1,
        "solution_status": solution_status,
        "diagnostics": diagnostics,
    }
    if with_plan:
        payload.update(_verified_plan_fields())
    return payload


def test_schema_and_optimal_example_are_valid(
    validator: Draft202012Validator,
) -> None:
    example = json.loads(OPTIMAL_EXAMPLE_PATH.read_text(encoding="utf-8"))

    validator.validate(example)
    assert example["cut_patterns"][0] == {
        "stock_id": "stock-10",
        "piece_counts": {"demand-4": 1, "demand-6": 1},
        "repetitions": 1,
    }


@pytest.mark.parametrize(
    "payload",
    [
        _payload(
            "optimal",
            _diagnostics(
                "completed",
                "optimal",
                completed_objective_stages=[
                    "stock_piece_count",
                    "total_leftover",
                    "distinct_pattern_count",
                ],
                lower_bound=2,
                upper_bound=2,
            ),
            with_plan=True,
        ),
        _payload(
            "feasible",
            _diagnostics(
                "time_limit",
                "feasible",
                completed_objective_stages=["stock_piece_count"],
                lower_bound=2,
                upper_bound=2,
            ),
            with_plan=True,
        ),
        _payload(
            "infeasible",
            _diagnostics("completed", "infeasible"),
        ),
        _payload(
            "limit",
            _diagnostics(
                "time_limit",
                "limit_reached",
                completed_objective_stages=["stock_piece_count"],
                lower_bound=1,
            ),
        ),
        _payload(
            "limit",
            _diagnostics(
                "time_limit",
                "limit_reached",
                completed_objective_stages=["stock_piece_count"],
                lower_bound=1,
                upper_bound=2,
            ),
            with_plan=True,
        ),
        _payload(
            "unsupported",
            _diagnostics("unsupported_problem", "not_run"),
        ),
        _payload(
            "error",
            _diagnostics("backend_failure", "failed"),
        ),
    ],
    ids=[
        "optimal",
        "feasible",
        "infeasible",
        "limit-without-plan",
        "limit-with-plan",
        "unsupported",
        "error",
    ],
)
def test_every_public_status_has_an_unambiguous_representation(
    validator: Draft202012Validator,
    payload: dict[str, object],
) -> None:
    validator.validate(payload)


@pytest.mark.parametrize("solution_status", ["optimal", "feasible"])
def test_plan_statuses_require_the_whole_verified_plan_group(
    validator: Draft202012Validator,
    solution_status: str,
) -> None:
    backend_status = solution_status
    stages = (
        [
            "stock_piece_count",
            "total_leftover",
            "distinct_pattern_count",
        ]
        if solution_status == "optimal"
        else []
    )
    payload = _payload(
        solution_status,
        _diagnostics("completed", backend_status, completed_objective_stages=stages),
        with_plan=True,
    )

    for missing_member in (
        "cut_patterns",
        "objective_value",
        "verification_report",
    ):
        incomplete = deepcopy(payload)
        del incomplete[missing_member]
        with pytest.raises(ValidationError):
            validator.validate(incomplete)


@pytest.mark.parametrize(
    "solution_status, diagnostics",
    [
        ("infeasible", _diagnostics("completed", "infeasible")),
        (
            "unsupported",
            _diagnostics("unsupported_problem", "not_run"),
        ),
        ("error", _diagnostics("internal_error", "not_run")),
    ],
)
def test_non_plan_statuses_forbid_plan_placeholders(
    validator: Draft202012Validator,
    solution_status: str,
    diagnostics: dict[str, object],
) -> None:
    payload = _payload(solution_status, diagnostics)

    for member, placeholder in (
        ("cut_patterns", []),
        ("objective_value", None),
        ("verification_report", None),
    ):
        invalid = deepcopy(payload)
        invalid[member] = placeholder
        with pytest.raises(ValidationError):
            validator.validate(invalid)


def test_limit_requires_all_or_none_of_the_verified_plan_group(
    validator: Draft202012Validator,
) -> None:
    payload = _payload(
        "limit",
        _diagnostics("time_limit", "limit_reached"),
    )
    payload["cut_patterns"] = _verified_plan_fields()["cut_patterns"]

    with pytest.raises(ValidationError):
        validator.validate(payload)


def test_limit_without_a_verified_plan_cannot_report_an_upper_bound(
    validator: Draft202012Validator,
) -> None:
    payload = _payload(
        "limit",
        _diagnostics("time_limit", "limit_reached", upper_bound=2),
    )

    with pytest.raises(ValidationError):
        validator.validate(payload)


def test_a_published_plan_requires_a_valid_verification_report(
    validator: Draft202012Validator,
) -> None:
    payload = _payload(
        "feasible",
        _diagnostics("completed", "feasible"),
        with_plan=True,
    )
    payload["verification_report"] = {
        "result": "invalid",
        "findings": [
            {
                "code": "objective_mismatch",
                "message": "Reported objective does not match the plan.",
            }
        ],
    }

    with pytest.raises(ValidationError):
        validator.validate(payload)


def test_standalone_verification_vocabulary_covers_both_results(
    schema: dict[str, object],
) -> None:
    report_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": "#/$defs/verificationReport",
        "$defs": schema["$defs"],
    }
    report_validator = Draft202012Validator(report_schema)

    report_validator.validate({"result": "valid", "findings": []})
    report_validator.validate(
        {
            "result": "invalid",
            "findings": [
                {
                    "code": "unknown_demand_id",
                    "message": "The pattern refers to an unknown demand.",
                    "demand_id": "missing-demand",
                    "pattern_index": 0,
                }
            ],
        }
    )


def test_status_diagnostics_cannot_overstate_proof(
    validator: Draft202012Validator,
) -> None:
    payload = _payload(
        "limit",
        _diagnostics(
            "time_limit",
            "limit_reached",
            completed_objective_stages=[
                "stock_piece_count",
                "total_leftover",
                "distinct_pattern_count",
            ],
        ),
    )

    with pytest.raises(ValidationError):
        validator.validate(payload)


def test_error_diagnostics_cannot_carry_proof_or_bounds(
    validator: Draft202012Validator,
) -> None:
    payload = _payload(
        "error",
        _diagnostics(
            "backend_failure",
            "failed",
            completed_objective_stages=["stock_piece_count"],
            lower_bound=1,
        ),
    )

    with pytest.raises(ValidationError):
        validator.validate(payload)


def test_cut_pattern_counts_and_repetitions_are_distinct_positive_values(
    validator: Draft202012Validator,
) -> None:
    payload = _payload(
        "feasible",
        _diagnostics("completed", "feasible"),
        with_plan=True,
    )
    pattern = payload["cut_patterns"][0]

    assert pattern["piece_counts"] == {"demand-4": 1, "demand-6": 1}
    assert pattern["repetitions"] == 2

    pattern["piece_counts"]["demand-4"] = 0
    with pytest.raises(ValidationError):
        validator.validate(payload)
