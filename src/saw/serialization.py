"""JSON serialization boundary for version 1 Saw problem requests.

This module converts a versioned JSON problem request into the existing
immutable :class:`~saw.models.Problem`, :class:`~saw.models.Stock`, and
:class:`~saw.models.Demand` models. The full version 1 JSON problem format
contract is documented in ``docs/formats.md``; the companion structural
schema is ``schemas/problem-v1.schema.json``. This module is the
authoritative implementation of the rules JSON Schema cannot express
(duplicate member names, integer spelling, and ID uniqueness), so it does
not depend on a JSON-schema library at run time.

Only JSON *shape* is enforced here: JSON syntax, the closed set of object
members, and primitive types and spelling. Semantic rules -- non-empty IDs,
positive lengths and quantities, and stock/demand ID uniqueness -- are
enforced by :func:`saw.validation.validate_problem`, not reimplemented in
this module, so there is exactly one place those rules live.

This module knows nothing about a command-line interface, standard streams,
exit codes, or input sources. It only turns JSON text that has already been
read into a :class:`~saw.models.Problem`, or raises
:class:`~saw.exceptions.SerializationError`.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable

from saw.exceptions import SerializationError, SerializationErrorCode, ValidationError
from saw.models import Demand, Problem, Stock
from saw.validation import validate_problem

__all__ = ("SUPPORTED_FORMAT_VERSION", "problem_from_json")

#: The only ``format_version`` value this module accepts.
SUPPORTED_FORMAT_VERSION = 1

_PROBLEM_MEMBERS = frozenset({"format_version", "stocks", "demands"})
_STOCK_MEMBERS = frozenset({"stock_id", "material_id", "length", "quantity"})
_DEMAND_MEMBERS = frozenset({"demand_id", "material_id", "length", "quantity"})


class _JsonSyntaxError(ValueError):
    """A JSON syntax problem this module rejects beyond RFC 8259 parsing."""


def problem_from_json(text: str) -> Problem:
    """Parse a version 1 JSON problem request into a :class:`Problem`.

    Args:
        text: Already-read JSON text, as described by ``docs/formats.md``.

    Returns:
        The equivalent immutable :class:`Problem`.

    Raises:
        SerializationError: if ``text`` is not syntactically valid JSON
            (including duplicate object member names or non-standard
            numeric constants such as ``NaN``); if the JSON does not
            structurally match version 1 of the problem format (missing or
            unknown members, or a primitive value with the wrong JSON type
            or spelling); if ``format_version`` is a well-formed integer
            other than ``1``; or if the assembled problem fails semantic
            validation performed by :func:`saw.validation.validate_problem`.
    """
    payload = _load_json(text)
    _require_object(payload, "problem")
    _require_exact_members(payload, _PROBLEM_MEMBERS, "problem")
    _check_format_version(payload["format_version"])

    stocks = _parse_records(payload["stocks"], "stocks", "stock", _STOCK_MEMBERS, _build_stock)
    demands = _parse_records(
        payload["demands"], "demands", "demand", _DEMAND_MEMBERS, _build_demand
    )

    problem = Problem(stocks=stocks, demands=demands)

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


def _load_json(text: str) -> object:
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


def _reject_duplicate_members(pairs: Iterable[tuple[str, object]]) -> dict[str, object]:
    members: dict[str, object] = {}
    for name, value in pairs:
        if name in members:
            raise _JsonSyntaxError(f"Duplicate JSON object member name: {name!r}.")
        members[name] = value
    return members


def _reject_non_standard_constant(constant: str) -> None:
    raise _JsonSyntaxError(
        f"Non-standard JSON numeric constant {constant!r} is not permitted."
    )


# --- Structural shape --------------------------------------------------


def _require_object(
    value: object,
    kind: str,
    *,
    record_index: int | None = None,
) -> None:
    if not isinstance(value, dict):
        raise SerializationError(
            f"{_record_label(kind, record_index)} must be a JSON object.",
            code=SerializationErrorCode.SCHEMA_VIOLATION,
            record=kind,
            record_index=record_index,
        )


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
            f"Unsupported format_version: {value}. Only "
            f"{SUPPORTED_FORMAT_VERSION} is supported.",
            code=SerializationErrorCode.UNSUPPORTED_FORMAT_VERSION,
            field="format_version",
        )


def _parse_records(
    value: object,
    array_field: str,
    record_kind: str,
    expected_members: frozenset[str],
    build: Callable[[dict[str, object], int], object],
) -> tuple[object, ...]:
    if not isinstance(value, list):
        raise SerializationError(
            f"{array_field} must be a JSON array.",
            code=SerializationErrorCode.SCHEMA_VIOLATION,
            field=array_field,
        )

    records = []
    for index, item in enumerate(value):
        _require_object(item, record_kind, record_index=index)
        _require_exact_members(item, expected_members, record_kind, record_index=index)
        records.append(build(item, index))
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
    # bool is a subclass of int in Python, so it must be rejected explicitly;
    # JSON's own true/false spelling must not satisfy an integer field.
    if isinstance(value, bool) or not isinstance(value, int):
        raise SerializationError(
            f"{record_kind}[{index}].{field} must use JSON integer spelling.",
            code=SerializationErrorCode.SCHEMA_VIOLATION,
            record=record_kind,
            record_index=index,
            field=field,
        )
    return value
