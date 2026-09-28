# format is explained in docs/formats.md and src/saw/schemas/
# reading only checks the json shape, value rules like positive lengths
# are checked in validation.py so they live in one place
# nothing here knows about the cli, files or exit codes

from __future__ import annotations

import codecs
from collections.abc import Callable, Iterable
import json
import math
import re
from typing import TypeVar

from saw.exceptions import (
    ErrorCode,
    SerializationError,
    SerializationErrorCode,
    ValidationError,
)
from saw.models import (
    CutPattern,
    Demand,
    Diagnostics,
    ObjectiveValue,
    Problem,
    Solution,
    SolutionStatus,
    Stock,
    VerificationFinding,
    VerificationReport,
)
from saw.validation import validate_problem

__all__ = (
    "SUPPORTED_FORMAT_VERSION",
    "json_from_error",
    "json_from_solution",
    "problem_from_bytes",
    "problem_from_json",
)

# the only format_version accepted
SUPPORTED_FORMAT_VERSION = 1

# a stock or demand built from one json record
_Record = TypeVar("_Record")

# the exact fields each json object must have, no more and no less
_PROBLEM_MEMBERS = frozenset({"format_version", "stocks", "demands"})
_STOCK_MEMBERS = frozenset({"stock_id", "material_id", "length", "quantity"})
_DEMAND_MEMBERS = frozenset({"demand_id", "material_id", "length", "quantity"})

# the verified plan is cut_patterns, objective_value and verification_report
# these statuses must always include it, see docs/formats.md
_STATUSES_REQUIRING_VERIFIED_PLAN = frozenset({SolutionStatus.OPTIMAL, SolutionStatus.FEASIBLE})

# these statuses must never include it
_STATUSES_FORBIDDING_VERIFIED_PLAN = frozenset(
    {SolutionStatus.INFEASIBLE, SolutionStatus.UNSUPPORTED, SolutionStatus.ERROR}
)

# limit includes the whole plan if a verified one was found, otherwise none of it
_STATUS_WITH_OPTIONAL_VERIFIED_PLAN = SolutionStatus.LIMIT


# raised for json that python's parser accepts but we dont, like duplicate keys or NaN
class _JsonSyntaxError(ValueError):
    pass


# parses a problem from raw bytes, the bytes are decoded as utf-8 first
def problem_from_bytes(data: bytes) -> Problem:
    return problem_from_json(_decode_problem_bytes(data))


# turns bytes into text, raises INVALID_JSON if they are empty, not utf-8,
# or start with a utf-16/utf-32 byte order mark
# a utf-8 byte order mark at the start is ignored
def _decode_problem_bytes(data: bytes) -> str:
    # the utf-32 marks start with the same bytes as the utf-16 ones,
    # so this check catches both
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        raise SerializationError(
            "Input starts with a UTF-16 or UTF-32 byte order mark; JSON input must be UTF-8.",
            code=SerializationErrorCode.INVALID_JSON,
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SerializationError(
            f"Input is not valid UTF-8: byte 0x{data[error.start]:02x} at "
            f"offset {error.start} cannot be decoded.",
            code=SerializationErrorCode.INVALID_JSON,
        ) from None
    text = text.removeprefix("\ufeff")
    # only json whitespace counts as empty, anything else is left to the json parser
    if not text.strip(" \t\r\n"):
        raise SerializationError(
            "Input is empty; expected exactly one JSON problem document.",
            code=SerializationErrorCode.INVALID_JSON,
        )
    return text


# turns an error code and message into the json error output
# message must not contain file paths, exception names or tracebacks
def json_from_error(code: ErrorCode, message: str) -> str:
    return json.dumps(
        {
            "format_version": SUPPORTED_FORMAT_VERSION,
            "error": {"code": ErrorCode(code).value, "message": message},
        },
        allow_nan=False,
    )


# parses json text into a problem, checks run in order:
# json syntax -> object shape -> format_version -> fields -> validate_problem
def problem_from_json(text: str) -> Problem:
    if not isinstance(text, str):
        # json.loads would accept bytes and guess the encoding,
        # bytes must go through problem_from_bytes instead
        raise TypeError(
            f"problem_from_json() expects str, not {type(text).__name__}; "
            "use problem_from_bytes() for undecoded input."
        )
    payload = _require_object(_load_json(text), "problem")
    _require_exact_members(payload, _PROBLEM_MEMBERS, "problem")
    _check_format_version(payload["format_version"])

    stocks = _parse_records(payload["stocks"], "stocks", "stock", _STOCK_MEMBERS, _build_stock)
    demands = _parse_records(
        payload["demands"], "demands", "demand", _DEMAND_MEMBERS, _build_demand
    )

    problem = Problem(stocks=stocks, demands=demands)

    # value rule failures are turned into SEMANTIC_VALIDATION
    try:
        validate_problem(problem)
    except ValidationError as error:
        raise SerializationError(
            str(error),
            code=SerializationErrorCode.SEMANTIC_VALIDATION,
            record=error.record,
            record_index=error.record_index,
            field=error.field,
        ) from error

    return problem


# --- JSON syntax -----------------------------------------------------------


# deepest nesting of arrays/objects allowed, a real problem only nests 3 deep
# without this limit, deep input would fail differently on different platforms
# this is only here to not run out of stack space in recursive calls
MAX_NESTING_DEPTH = 64

# matches a whole json string (so brackets inside strings are skipped) or one bracket
_STRING_OR_BRACKET = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"|[\[\]{}]')


# counts brackets before parsing and raises INVALID_JSON if nesting is too deep
def _check_nesting_depth(text: str) -> None:
    depth = 0
    for match in _STRING_OR_BRACKET.finditer(text):
        token = match.group()
        if token in "[{":
            depth += 1
            if depth > MAX_NESTING_DEPTH:
                raise SerializationError(
                    "Input is not valid JSON: arrays or objects are nested "
                    f"more than {MAX_NESTING_DEPTH} levels deep.",
                    code=SerializationErrorCode.INVALID_JSON,
                )
        elif token in "]}":
            depth -= 1


# parses the json text, turning every syntax failure into INVALID_JSON
def _load_json(text: str) -> object:
    _check_nesting_depth(text)
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_members,
            parse_constant=_reject_non_standard_constant,
        )
    except json.JSONDecodeError as error:
        raise SerializationError(
            f"Input is not valid JSON: {error}",
            code=SerializationErrorCode.INVALID_JSON,
        ) from error
    except _JsonSyntaxError as error:
        raise SerializationError(
            str(error),
            code=SerializationErrorCode.INVALID_JSON,
        ) from error
    except RecursionError:
        # backup for _check_nesting_depth, if python still runs out of stack
        # it is bad input, not an internal error
        raise SerializationError(
            "Input is not valid JSON: arrays or objects are nested too deeply.",
            code=SerializationErrorCode.INVALID_JSON,
        ) from None


# used by json.loads to build objects, raises on a key that appears twice
# (by default python would just keep the last one)
def _reject_duplicate_members(pairs: Iterable[tuple[str, object]]) -> dict[str, object]:
    members: dict[str, object] = {}
    for name, value in pairs:
        if name in members:
            raise _JsonSyntaxError(f"Duplicate JSON object member name: {name!r}.")
        members[name] = value
    return members


# used by json.loads, raises on NaN, Infinity and -Infinity which are not real json
def _reject_non_standard_constant(constant: str) -> None:
    raise _JsonSyntaxError(f"Non-standard JSON numeric constant {constant!r} is not permitted.")


# --- Structural shape --------------------------------------------------


def _require_object(
    value: object,
    kind: str,
    *,
    record_index: int | None = None,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise SerializationError(
            f"{_record_label(kind, record_index)} must be a JSON object.",
            code=SerializationErrorCode.SCHEMA_VIOLATION,
            record=kind,
            record_index=record_index,
        )
    return value


def _require_exact_members(
    obj: dict[str, object],
    expected: frozenset[str],
    kind: str,
    *,
    record_index: int | None = None,
) -> None:
    actual = set(obj)
    missing = expected - actual
    unknown = actual - expected
    if not missing and not unknown:
        return

    parts = []
    if missing:
        parts.append(f"missing {', '.join(sorted(missing))}")
    if unknown:
        parts.append(f"unknown {', '.join(sorted(unknown))}")
    raise SerializationError(
        f"{_record_label(kind, record_index)} has invalid members: {'; '.join(parts)}.",
        code=SerializationErrorCode.SCHEMA_VIOLATION,
        record=kind,
        record_index=record_index,
    )


def _record_label(kind: str, record_index: int | None) -> str:
    if record_index is None:
        return f"The {kind}"
    return f"{kind}[{record_index}]"


def _check_format_version(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SerializationError(
            "format_version must use JSON integer spelling.",
            code=SerializationErrorCode.SCHEMA_VIOLATION,
            field="format_version",
        )
    if value != SUPPORTED_FORMAT_VERSION:
        raise SerializationError(
            f"Unsupported format_version: {value}. Only {SUPPORTED_FORMAT_VERSION} is supported.",
            code=SerializationErrorCode.UNSUPPORTED_FORMAT_VERSION,
            field="format_version",
        )


def _parse_records(
    value: object,
    array_field: str,
    record_kind: str,
    expected_members: frozenset[str],
    build: Callable[[dict[str, object], int], _Record],
) -> tuple[_Record, ...]:
    if not isinstance(value, list):
        raise SerializationError(
            f"{array_field} must be a JSON array.",
            code=SerializationErrorCode.SCHEMA_VIOLATION,
            field=array_field,
        )

    records = []
    for index, item in enumerate(value):
        record = _require_object(item, record_kind, record_index=index)
        _require_exact_members(record, expected_members, record_kind, record_index=index)
        records.append(build(record, index))
    return tuple(records)


def _build_stock(item: dict[str, object], index: int) -> Stock:
    return Stock(
        stock_id=_require_string(item["stock_id"], "stock", index, "stock_id"),
        material_id=_require_string(item["material_id"], "stock", index, "material_id"),
        length=_require_integer(item["length"], "stock", index, "length"),
        quantity=_require_integer(item["quantity"], "stock", index, "quantity"),
    )


def _build_demand(item: dict[str, object], index: int) -> Demand:
    return Demand(
        demand_id=_require_string(item["demand_id"], "demand", index, "demand_id"),
        material_id=_require_string(item["material_id"], "demand", index, "material_id"),
        length=_require_integer(item["length"], "demand", index, "length"),
        quantity=_require_integer(item["quantity"], "demand", index, "quantity"),
    )


def _require_string(value: object, record_kind: str, index: int, field: str) -> str:
    if not isinstance(value, str):
        raise SerializationError(
            f"{record_kind}[{index}].{field} must use JSON string spelling.",
            code=SerializationErrorCode.SCHEMA_VIOLATION,
            record=record_kind,
            record_index=index,
            field=field,
        )
    return value


def _require_integer(value: object, record_kind: str, index: int, field: str) -> int:
    # bool counts as int in python, so true/false has to be rejected separately
    if isinstance(value, bool) or not isinstance(value, int):
        raise SerializationError(
            f"{record_kind}[{index}].{field} must use JSON integer spelling.",
            code=SerializationErrorCode.SCHEMA_VIOLATION,
            record=record_kind,
            record_index=index,
            field=field,
        )
    return value


# --- Solution serialization (Python -> JSON) --------------------------------


# turns a solution into json text
# whether the verified plan is included depends only on the status, never on
# cut_patterns being empty, an empty list is a real plan that uses no stock
# raises SCHEMA_VIOLATION if the solution contradicts its own status
def json_from_solution(solution: Solution) -> str:
    payload = _solution_payload(solution)
    try:
        return json.dumps(payload, allow_nan=False)
    except ValueError as error:
        # allow_nan=False raises instead of writing NaN/Infinity, which is not real json
        raise SerializationError(
            "diagnostics.elapsed_time must be a finite JSON number.",
            code=SerializationErrorCode.SCHEMA_VIOLATION,
            field="elapsed_time",
        ) from error


def _solution_payload(solution: Solution) -> dict[str, object]:
    includes_plan = _includes_verified_plan(solution)
    objective_value = solution.objective_value
    report = solution.verification_report

    payload: dict[str, object] = {
        "format_version": SUPPORTED_FORMAT_VERSION,
        "solution_status": solution.solution_status.value,
    }

    if includes_plan:
        # only narrows the types, _includes_verified_plan already raised if
        # either one is missing
        assert objective_value is not None and report is not None
        payload["cut_patterns"] = [
            _cut_pattern_payload(cut_pattern) for cut_pattern in solution.cut_patterns
        ]
        payload["objective_value"] = _objective_value_payload(objective_value)

    payload["diagnostics"] = _diagnostics_payload(solution.diagnostics)

    if includes_plan:
        assert report is not None
        payload["verification_report"] = _verification_report_payload(report)

    return payload


# decides if the verified plan goes in the output based on the status
# also raises if the solution contradicts its status, so when this returns
# True every plan field is guaranteed to be there
def _includes_verified_plan(solution: Solution) -> bool:
    status = solution.solution_status
    has_report = solution.verification_report is not None

    if solution.verification_report is not None and not solution.verification_report.is_valid:
        raise SerializationError(
            "verification_report must be valid to be attached to a "
            "published solution; an invalid report is never evidence for a "
            "published plan.",
            code=SerializationErrorCode.SCHEMA_VIOLATION,
            field="verification_report",
        )

    if status in _STATUSES_FORBIDDING_VERIFIED_PLAN:
        if has_report:
            raise SerializationError(
                f"solution_status {status.value!r} forbids a verified plan, "
                "but verification_report is present.",
                code=SerializationErrorCode.SCHEMA_VIOLATION,
                field="verification_report",
            )
        return False

    if status in _STATUSES_REQUIRING_VERIFIED_PLAN:
        should_include = True
    else:
        assert status is _STATUS_WITH_OPTIONAL_VERIFIED_PLAN
        should_include = has_report

    if not should_include:
        return False

    if not has_report:
        raise SerializationError(
            f"solution_status {status.value!r} requires a verified plan, "
            "but verification_report is missing.",
            code=SerializationErrorCode.SCHEMA_VIOLATION,
            field="verification_report",
        )
    if solution.objective_value is None:
        raise SerializationError(
            f"solution_status {status.value!r} requires a verified plan, "
            "but objective_value is missing.",
            code=SerializationErrorCode.SCHEMA_VIOLATION,
            field="objective_value",
        )
    return True


def _cut_pattern_payload(cut_pattern: CutPattern) -> dict[str, object]:
    return {
        "stock_id": cut_pattern.stock_id,
        "piece_counts": dict(cut_pattern.piece_counts),
        "repetitions": cut_pattern.repetitions,
    }


def _objective_value_payload(objective_value: ObjectiveValue) -> dict[str, object]:
    return {
        "stock_piece_count": objective_value.stock_piece_count,
        "total_leftover": objective_value.total_leftover,
        "distinct_pattern_count": objective_value.distinct_pattern_count,
    }


def _diagnostics_payload(diagnostics: Diagnostics) -> dict[str, object]:
    if not math.isfinite(diagnostics.elapsed_time):
        raise SerializationError(
            "diagnostics.elapsed_time must be a finite JSON number.",
            code=SerializationErrorCode.SCHEMA_VIOLATION,
            field="elapsed_time",
        )

    # backend_id and backend_version are left out on purpose, adding a field
    # to the json needs a new format_version, see docs/formats.md "Diagnostics"
    return {
        "termination_reason": diagnostics.termination_reason.value,
        "backend_status": diagnostics.backend_status.value,
        "elapsed_time": diagnostics.elapsed_time,
        "patterns_generated": diagnostics.patterns_generated,
        "iterations": diagnostics.iterations,
        "completed_objective_stages": [
            stage.value for stage in diagnostics.completed_objective_stages
        ],
        "lower_bound": diagnostics.lower_bound,
        "upper_bound": diagnostics.upper_bound,
    }


def _verification_report_payload(report: VerificationReport) -> dict[str, object]:
    return {
        "result": report.result.value,
        "findings": [_verification_finding_payload(finding) for finding in report.findings],
    }


def _verification_finding_payload(finding: VerificationFinding) -> dict[str, object]:
    payload: dict[str, object] = {
        "code": finding.code.value,
        "message": finding.message,
    }
    # optional fields are left out when missing instead of being written as null
    if finding.stock_id is not None:
        payload["stock_id"] = finding.stock_id
    if finding.demand_id is not None:
        payload["demand_id"] = finding.demand_id
    if finding.pattern_index is not None:
        payload["pattern_index"] = finding.pattern_index
    return payload
