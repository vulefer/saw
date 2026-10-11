import json
from pathlib import Path

import pytest

from saw import (
    BackendStatus,
    CutPattern,
    Demand,
    Diagnostics,
    ObjectiveStage,
    ObjectiveValue,
    Problem,
    SerializationError,
    SerializationErrorCode,
    Solution,
    SolutionStatus,
    Stock,
    TerminationReason,
    VerificationFinding,
    VerificationFindingCode,
    VerificationReport,
    json_from_solution,
    problem_from_json,
)
from saw.patterns import Pattern
import saw.serialization

REPOSITORY_ROOT = Path(__file__).parents[2]
VALID_EXAMPLE_PATH = REPOSITORY_ROOT / "examples" / "json" / "problem-v1-valid.json"
OPTIMAL_SOLUTION_EXAMPLE_PATH = REPOSITORY_ROOT / "examples" / "json" / "solution-v1-optimal.json"

MINIMAL_VALID_JSON = json.dumps({"format_version": 1, "stocks": [], "demands": []})


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
    duplicated = '{"format_version": 1, "format_version": 1, "stocks": [], "demands": []}'

    with pytest.raises(SerializationError) as excinfo:
        problem_from_json(duplicated)

    assert excinfo.value.code is SerializationErrorCode.INVALID_JSON


def test_duplicate_member_names_inside_a_stock_object_are_rejected() -> None:
    duplicated = json.dumps(_valid_payload()).replace('"length": 10', '"length": 10, "length": 10')

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


# =============================================================================
# json_from_solution: Solution (Python) -> version 1 JSON text
# =============================================================================


def _diagnostics(**overrides: object) -> Diagnostics:
    fields: dict[str, object] = dict(
        termination_reason=TerminationReason.COMPLETED,
        backend_status=BackendStatus.OPTIMAL,
        elapsed_time=0.01,
        patterns_generated=4,
        iterations=3,
        completed_objective_stages=(
            ObjectiveStage.STOCK_PIECE_COUNT,
            ObjectiveStage.TOTAL_LEFTOVER,
            ObjectiveStage.DISTINCT_PATTERN_COUNT,
        ),
        lower_bound=1,
        upper_bound=1,
    )
    fields.update(overrides)
    return Diagnostics(**fields)


def _cut_pattern(
    stock_id: str = "stock-10",
    piece_counts: dict[str, int] | None = None,
    repetitions: int = 1,
) -> CutPattern:
    counts = {"demand-4": 1, "demand-6": 1} if piece_counts is None else piece_counts
    return CutPattern(pattern=Pattern(stock_id, counts), repetitions=repetitions)


_OBJECTIVE_VALUE = ObjectiveValue(stock_piece_count=1, total_leftover=0, distinct_pattern_count=1)
_VALID_REPORT = VerificationReport(findings=())

_ALWAYS_REQUIRED_MEMBERS = frozenset({"format_version", "solution_status", "diagnostics"})
_VERIFIED_PLAN_MEMBERS = frozenset({"cut_patterns", "objective_value", "verification_report"})


def _optimal_solution(**overrides: object) -> Solution:
    fields: dict[str, object] = dict(
        solution_status=SolutionStatus.OPTIMAL,
        cut_patterns=(_cut_pattern(),),
        objective_value=_OBJECTIVE_VALUE,
        diagnostics=_diagnostics(),
        verification_report=_VALID_REPORT,
    )
    fields.update(overrides)
    return Solution(**fields)


def _feasible_solution(**overrides: object) -> Solution:
    fields: dict[str, object] = dict(
        solution_status=SolutionStatus.FEASIBLE,
        cut_patterns=(_cut_pattern(),),
        objective_value=_OBJECTIVE_VALUE,
        diagnostics=_diagnostics(
            termination_reason=TerminationReason.TIME_LIMIT,
            backend_status=BackendStatus.FEASIBLE,
            completed_objective_stages=(ObjectiveStage.STOCK_PIECE_COUNT,),
        ),
        verification_report=_VALID_REPORT,
    )
    fields.update(overrides)
    return Solution(**fields)


def _infeasible_solution(**overrides: object) -> Solution:
    fields: dict[str, object] = dict(
        solution_status=SolutionStatus.INFEASIBLE,
        cut_patterns=(),
        objective_value=None,
        diagnostics=_diagnostics(
            backend_status=BackendStatus.INFEASIBLE,
            completed_objective_stages=(),
            lower_bound=None,
            upper_bound=None,
        ),
        verification_report=None,
    )
    fields.update(overrides)
    return Solution(**fields)


def _limit_without_plan_solution(**overrides: object) -> Solution:
    fields: dict[str, object] = dict(
        solution_status=SolutionStatus.LIMIT,
        cut_patterns=(),
        objective_value=None,
        diagnostics=_diagnostics(
            termination_reason=TerminationReason.TIME_LIMIT,
            backend_status=BackendStatus.LIMIT_REACHED,
            completed_objective_stages=(ObjectiveStage.STOCK_PIECE_COUNT,),
            lower_bound=1,
            upper_bound=None,
        ),
        verification_report=None,
    )
    fields.update(overrides)
    return Solution(**fields)


def _limit_with_plan_solution(**overrides: object) -> Solution:
    fields: dict[str, object] = dict(
        solution_status=SolutionStatus.LIMIT,
        cut_patterns=(_cut_pattern(),),
        objective_value=_OBJECTIVE_VALUE,
        diagnostics=_diagnostics(
            termination_reason=TerminationReason.TIME_LIMIT,
            backend_status=BackendStatus.LIMIT_REACHED,
            completed_objective_stages=(ObjectiveStage.STOCK_PIECE_COUNT,),
            upper_bound=1,
        ),
        verification_report=_VALID_REPORT,
    )
    fields.update(overrides)
    return Solution(**fields)


def _unsupported_solution(**overrides: object) -> Solution:
    fields: dict[str, object] = dict(
        solution_status=SolutionStatus.UNSUPPORTED,
        cut_patterns=(),
        objective_value=None,
        diagnostics=_diagnostics(
            termination_reason=TerminationReason.UNSUPPORTED_PROBLEM,
            backend_status=BackendStatus.NOT_RUN,
            completed_objective_stages=(),
            lower_bound=None,
            upper_bound=None,
        ),
        verification_report=None,
    )
    fields.update(overrides)
    return Solution(**fields)


def _error_solution(**overrides: object) -> Solution:
    fields: dict[str, object] = dict(
        solution_status=SolutionStatus.ERROR,
        cut_patterns=(),
        objective_value=None,
        diagnostics=_diagnostics(
            termination_reason=TerminationReason.BACKEND_FAILURE,
            backend_status=BackendStatus.FAILED,
            completed_objective_stages=(),
            lower_bound=None,
            upper_bound=None,
        ),
        verification_report=None,
    )
    fields.update(overrides)
    return Solution(**fields)


# --- Matches the documented example exactly ---------------------------------


def test_json_from_solution_matches_the_documented_optimal_example() -> None:
    solution = _optimal_solution()

    text = json_from_solution(solution)

    assert json.loads(text) == json.loads(OPTIMAL_SOLUTION_EXAMPLE_PATH.read_text(encoding="utf-8"))


# --- Every public status has direct, unambiguous coverage ------------------


@pytest.mark.parametrize(
    ("solution", "expects_verified_plan"),
    [
        (_optimal_solution(), True),
        (_feasible_solution(), True),
        (_infeasible_solution(), False),
        (_limit_without_plan_solution(), False),
        (_limit_with_plan_solution(), True),
        (_unsupported_solution(), False),
        (_error_solution(), False),
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
def test_verified_plan_group_presence_matches_the_status_table(
    solution: Solution, expects_verified_plan: bool
) -> None:
    payload = json.loads(json_from_solution(solution))

    present_plan_members = _VERIFIED_PLAN_MEMBERS & payload.keys()
    if expects_verified_plan:
        assert present_plan_members == _VERIFIED_PLAN_MEMBERS
    else:
        assert present_plan_members == set()

    # Forbidden means omitted outright -- never present as null or empty.
    assert payload.keys() == _ALWAYS_REQUIRED_MEMBERS | (
        _VERIFIED_PLAN_MEMBERS if expects_verified_plan else set()
    )


def test_an_empty_cut_patterns_array_is_a_real_verified_plan_not_an_absent_one() -> None:
    # A problem with no demand has a genuine, verified zero-stock optimal
    # plan. It must still carry the full verified-plan group.
    solution = _optimal_solution(
        cut_patterns=(),
        objective_value=ObjectiveValue(
            stock_piece_count=0, total_leftover=0, distinct_pattern_count=0
        ),
    )

    payload = json.loads(json_from_solution(solution))

    assert payload["cut_patterns"] == []
    assert payload["objective_value"] == {
        "stock_piece_count": 0,
        "total_leftover": 0,
        "distinct_pattern_count": 0,
    }
    assert payload["verification_report"] == {"result": "valid", "findings": []}


# --- IDs, objective components, stages, bounds round-trip exactly ----------


def test_cut_pattern_ids_and_piece_counts_round_trip_by_name_not_position() -> None:
    solution = _optimal_solution(
        cut_patterns=(
            _cut_pattern(
                stock_id="stock-a",
                piece_counts={"demand-z": 3, "demand-a": 2},
                repetitions=5,
            ),
        ),
        objective_value=ObjectiveValue(
            stock_piece_count=5, total_leftover=0, distinct_pattern_count=1
        ),
    )

    payload = json.loads(json_from_solution(solution))

    assert payload["cut_patterns"] == [
        {
            "stock_id": "stock-a",
            "piece_counts": {"demand-z": 3, "demand-a": 2},
            "repetitions": 5,
        }
    ]


def test_objective_value_components_round_trip_by_name() -> None:
    solution = _optimal_solution(
        objective_value=ObjectiveValue(
            stock_piece_count=7, total_leftover=13, distinct_pattern_count=2
        )
    )

    payload = json.loads(json_from_solution(solution))

    assert payload["objective_value"] == {
        "stock_piece_count": 7,
        "total_leftover": 13,
        "distinct_pattern_count": 2,
    }


def test_completed_objective_stages_round_trip_in_order() -> None:
    solution = _feasible_solution(
        diagnostics=_diagnostics(
            termination_reason=TerminationReason.TIME_LIMIT,
            backend_status=BackendStatus.FEASIBLE,
            completed_objective_stages=(
                ObjectiveStage.STOCK_PIECE_COUNT,
                ObjectiveStage.TOTAL_LEFTOVER,
            ),
        )
    )

    payload = json.loads(json_from_solution(solution))

    assert payload["diagnostics"]["completed_objective_stages"] == [
        "stock_piece_count",
        "total_leftover",
    ]


@pytest.mark.parametrize(
    ("lower_bound", "upper_bound"),
    [(None, None), (3, None), (None, 3), (3, 3)],
    ids=["both-null", "lower-only", "upper-only", "both-numeric"],
)
def test_bounds_round_trip_including_null(lower_bound: int | None, upper_bound: int | None) -> None:
    solution = _limit_without_plan_solution(
        diagnostics=_diagnostics(
            termination_reason=TerminationReason.TIME_LIMIT,
            backend_status=BackendStatus.LIMIT_REACHED,
            completed_objective_stages=(),
            lower_bound=lower_bound,
            upper_bound=upper_bound,
        )
    )

    payload = json.loads(json_from_solution(solution))

    assert payload["diagnostics"]["lower_bound"] == lower_bound
    assert payload["diagnostics"]["upper_bound"] == upper_bound


_JSON_DIAGNOSTICS_MEMBERS = frozenset(
    {
        "termination_reason",
        "backend_status",
        "elapsed_time",
        "patterns_generated",
        "iterations",
        "completed_objective_stages",
        "lower_bound",
        "upper_bound",
    }
)


def test_backend_id_and_backend_version_are_never_serialized() -> None:
    """docs/formats.md, "Python-only diagnostics fields": backend_id and
    backend_version exist only on the Python Diagnostics object. The
    version 1 JSON solution format is a closed contract, so
    json_from_solution() must never write them, however they are set on
    the Python side.
    """
    solution = _optimal_solution(
        diagnostics=_diagnostics(
            backend_id="exact_enumerated",
            backend_version="2.9.0",
        )
    )

    payload = json.loads(json_from_solution(solution))

    assert payload["diagnostics"].keys() == _JSON_DIAGNOSTICS_MEMBERS
    assert "backend_id" not in payload["diagnostics"]
    assert "backend_version" not in payload["diagnostics"]


def test_verification_report_round_trips_as_valid_with_empty_findings() -> None:
    solution = _optimal_solution()

    payload = json.loads(json_from_solution(solution))

    assert payload["verification_report"] == {"result": "valid", "findings": []}


@pytest.mark.parametrize(
    "finding",
    [
        VerificationFinding(
            code=VerificationFindingCode.UNKNOWN_DEMAND_ID,
            message="The pattern refers to an unknown demand.",
            demand_id="missing-demand",
            pattern_index=0,
        ),
        VerificationFinding(
            code=VerificationFindingCode.UNKNOWN_STOCK_ID,
            message="The pattern refers to an unknown stock.",
            stock_id="missing-stock",
            pattern_index=1,
        ),
        VerificationFinding(
            code=VerificationFindingCode.OBJECTIVE_MISMATCH,
            message="Reported objective does not match the plan.",
        ),
    ],
    ids=["demand-context", "stock-context", "no-context"],
)
def test_verification_findings_round_trip_present_context_only(
    finding: VerificationFinding,
) -> None:
    # A standalone (unattached) report is used here because a version 1
    # solution never carries an invalid report -- see
    # test_a_forbidden_or_invalid_verification_report_is_rejected below. This
    # exercises the same finding-payload logic that backs every attached
    # report's (always-empty) findings array.
    report = VerificationReport(findings=(finding,))

    payload = saw.serialization._verification_report_payload(report)

    expected: dict[str, object] = {"code": finding.code.value, "message": finding.message}
    if finding.stock_id is not None:
        expected["stock_id"] = finding.stock_id
    if finding.demand_id is not None:
        expected["demand_id"] = finding.demand_id
    if finding.pattern_index is not None:
        expected["pattern_index"] = finding.pattern_index

    assert payload == {"result": "invalid", "findings": [expected]}


# --- Fields invalid for a status are omitted, never null placeholders ------


@pytest.mark.parametrize(
    "solution",
    [
        _infeasible_solution(),
        _limit_without_plan_solution(),
        _unsupported_solution(),
        _error_solution(),
    ],
    ids=["infeasible", "limit-without-plan", "unsupported", "error"],
)
def test_non_plan_statuses_never_emit_plan_members_as_null(solution: Solution) -> None:
    payload = json.loads(json_from_solution(solution))

    for member in _VERIFIED_PLAN_MEMBERS:
        assert member not in payload


# --- Determinism -------------------------------------------------------------


def test_serialization_is_deterministic_for_equivalent_results() -> None:
    first = json_from_solution(_optimal_solution())
    second = json_from_solution(_optimal_solution())

    assert first == second


def test_serialization_is_deterministic_across_repeated_calls_on_one_value() -> None:
    solution = _limit_with_plan_solution()

    assert json_from_solution(solution) == json_from_solution(solution)


# --- Contract violations are rejected, not silently miswritten -------------


def test_a_status_requiring_a_plan_without_a_verification_report_is_rejected() -> None:
    solution = _optimal_solution(verification_report=None)

    with pytest.raises(SerializationError) as excinfo:
        json_from_solution(solution)

    assert excinfo.value.code is SerializationErrorCode.SCHEMA_VIOLATION
    assert excinfo.value.field == "verification_report"


def test_a_status_requiring_a_plan_without_an_objective_value_is_rejected() -> None:
    solution = _optimal_solution(objective_value=None)

    with pytest.raises(SerializationError) as excinfo:
        json_from_solution(solution)

    assert excinfo.value.code is SerializationErrorCode.SCHEMA_VIOLATION
    assert excinfo.value.field == "objective_value"


@pytest.mark.parametrize(
    "make_solution",
    [_infeasible_solution, _unsupported_solution, _error_solution],
    ids=["infeasible", "unsupported", "error"],
)
def test_a_forbidden_or_invalid_verification_report_is_rejected(
    make_solution,
) -> None:
    solution = make_solution(verification_report=_VALID_REPORT)

    with pytest.raises(SerializationError) as excinfo:
        json_from_solution(solution)

    assert excinfo.value.code is SerializationErrorCode.SCHEMA_VIOLATION
    assert excinfo.value.field == "verification_report"


def test_an_invalid_verification_report_cannot_be_attached_to_a_published_plan() -> None:
    invalid_report = VerificationReport(
        findings=(
            VerificationFinding(
                code=VerificationFindingCode.OBJECTIVE_MISMATCH,
                message="Reported objective does not match the plan.",
            ),
        )
    )
    solution = _feasible_solution(verification_report=invalid_report)

    with pytest.raises(SerializationError) as excinfo:
        json_from_solution(solution)

    assert excinfo.value.code is SerializationErrorCode.SCHEMA_VIOLATION
    assert excinfo.value.field == "verification_report"


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_elapsed_time_is_rejected(non_finite: float) -> None:
    solution = _infeasible_solution(
        diagnostics=_diagnostics(
            backend_status=BackendStatus.INFEASIBLE,
            completed_objective_stages=(),
            lower_bound=None,
            upper_bound=None,
            elapsed_time=non_finite,
        )
    )

    with pytest.raises(SerializationError) as excinfo:
        json_from_solution(solution)

    assert excinfo.value.code is SerializationErrorCode.SCHEMA_VIOLATION
    assert excinfo.value.field == "elapsed_time"


# --- problem_from_bytes: the byte-level decoding rules ----------------------


def test_bytes_input_parses_like_the_equivalent_text() -> None:
    raw = VALID_EXAMPLE_PATH.read_bytes()

    assert saw.problem_from_bytes(raw) == problem_from_json(raw.decode("utf-8"))


def test_bytes_input_ignores_one_leading_utf8_byte_order_mark() -> None:
    raw = VALID_EXAMPLE_PATH.read_bytes()

    assert saw.problem_from_bytes(b"\xef\xbb\xbf" + raw) == saw.problem_from_bytes(raw)


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b" \t\r\n",
        b"\xef\xbb\xbf",
        MINIMAL_VALID_JSON.encode("utf-16"),
        MINIMAL_VALID_JSON.encode("utf-32"),
        b'{"format_version": 1, "stocks": [], "demands": [], "x": "\xe9"}',
    ],
    ids=["empty", "whitespace", "bom-only", "utf-16", "utf-32", "latin-1"],
)
def test_bytes_that_are_not_one_utf8_json_text_are_invalid_json(raw: bytes) -> None:
    with pytest.raises(SerializationError) as caught:
        saw.problem_from_bytes(raw)

    assert caught.value.code is SerializationErrorCode.INVALID_JSON
    assert caught.value.error_code is saw.ErrorCode.INVALID_JSON


def test_problem_from_json_rejects_undecoded_bytes() -> None:
    with pytest.raises(TypeError, match="problem_from_bytes"):
        problem_from_json(MINIMAL_VALID_JSON.encode("utf-8"))


@pytest.mark.parametrize(
    ("code", "error_code"),
    [
        (SerializationErrorCode.INVALID_JSON, "invalid_json"),
        (SerializationErrorCode.UNSUPPORTED_FORMAT_VERSION, "unsupported_format_version"),
        (SerializationErrorCode.SCHEMA_VIOLATION, "invalid_problem"),
        (SerializationErrorCode.SEMANTIC_VALIDATION, "invalid_problem"),
    ],
)
def test_every_serialization_error_has_an_envelope_code(
    code: SerializationErrorCode,
    error_code: str,
) -> None:
    assert SerializationError("x", code=code).error_code == error_code


# --- json_from_error: the version 1 error envelope ---------------------------


def test_error_envelope_has_exactly_the_documented_members() -> None:
    document = json.loads(saw.json_from_error(saw.ErrorCode.INVALID_JSON, "Bad input."))

    assert document == {
        "format_version": 1,
        "error": {"code": "invalid_json", "message": "Bad input."},
    }


def test_error_envelope_rejects_an_undocumented_code() -> None:
    with pytest.raises(ValueError):
        saw.json_from_error("no_such_code", "message")


# --- nesting depth: one limit on every platform -----------------------------


def _nested(depth: int) -> str:
    return "[" * depth + "]" * depth


def test_nesting_at_the_limit_is_left_to_the_parser() -> None:
    # Valid JSON at the limit parses; it is only rejected as a non-object.
    with pytest.raises(SerializationError) as caught:
        problem_from_json(_nested(saw.serialization.MAX_NESTING_DEPTH))

    assert caught.value.code is SerializationErrorCode.SCHEMA_VIOLATION


@pytest.mark.parametrize(
    "text",
    [
        _nested(saw.serialization.MAX_NESTING_DEPTH + 1),
        # Deep enough to parse on a large C stack, yet exhaust a small one:
        # the outcome must not depend on which one this machine has.
        _nested(100_000),
        "[" * 100_000,
    ],
    ids=["one-past-the-limit", "closed-100k", "unclosed-100k"],
)
def test_nesting_past_the_limit_is_invalid_json(text: str) -> None:
    with pytest.raises(SerializationError) as caught:
        problem_from_json(text)

    assert caught.value.code is SerializationErrorCode.INVALID_JSON
    assert "nested" in str(caught.value)


def test_brackets_inside_strings_do_not_count_as_nesting() -> None:
    deep_text = "[" * 1_000 + '\\"' + "{" * 1_000
    problem = json.dumps(
        {
            "format_version": 1,
            "stocks": [_valid_stock(stock_id=deep_text)],
            "demands": [],
        }
    )

    assert problem_from_json(problem).stocks[0].stock_id == deep_text
