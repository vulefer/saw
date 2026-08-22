from collections.abc import Callable
from dataclasses import dataclass
from inspect import signature
from unittest.mock import Mock

import pytest

from saw import (
    Demand,
    Problem,
    Stock,
    ValidationError,
    ValidationErrorCode,
    validate_problem,
)


ProblemFactory = Callable[[], Problem]


@dataclass(frozen=True)
class PrimitiveRule:
    name: str
    valid_problem: ProblemFactory
    invalid_problem: ProblemFactory
    code: ValidationErrorCode
    record: str
    field: str


PRIMITIVE_RULES = (
    PrimitiveRule(
        "stock ID is a non-empty string",
        lambda: Problem((Stock("stock-10", "steel", 10, 1),), ()),
        lambda: Problem((Stock("", "steel", 10, 1),), ()),
        ValidationErrorCode.INVALID_STOCK_ID,
        "stock",
        "stock_id",
    ),
    PrimitiveRule(
        "stock material ID is a non-empty string",
        lambda: Problem((Stock("stock-10", "steel", 10, 1),), ()),
        lambda: Problem((Stock("stock-10", "", 10, 1),), ()),
        ValidationErrorCode.INVALID_STOCK_MATERIAL_ID,
        "stock",
        "material_id",
    ),
    PrimitiveRule(
        "stock length is a positive integer",
        lambda: Problem((Stock("stock-10", "steel", 10, 1),), ()),
        lambda: Problem((Stock("stock-10", "steel", 0, 1),), ()),
        ValidationErrorCode.INVALID_STOCK_LENGTH,
        "stock",
        "length",
    ),
    PrimitiveRule(
        "stock quantity is a positive integer",
        lambda: Problem((Stock("stock-10", "steel", 10, 1),), ()),
        lambda: Problem((Stock("stock-10", "steel", 10, False),), ()),
        ValidationErrorCode.INVALID_STOCK_QUANTITY,
        "stock",
        "quantity",
    ),
    PrimitiveRule(
        "demand ID is a non-empty string",
        lambda: Problem((), (Demand("demand-6", "steel", 6, 1),)),
        lambda: Problem((), (Demand("", "steel", 6, 1),)),
        ValidationErrorCode.INVALID_DEMAND_ID,
        "demand",
        "demand_id",
    ),
    PrimitiveRule(
        "demand material ID is a non-empty string",
        lambda: Problem((), (Demand("demand-6", "steel", 6, 1),)),
        lambda: Problem((), (Demand("demand-6", "", 6, 1),)),
        ValidationErrorCode.INVALID_DEMAND_MATERIAL_ID,
        "demand",
        "material_id",
    ),
    PrimitiveRule(
        "demand length is a positive integer",
        lambda: Problem((), (Demand("demand-6", "steel", 6, 1),)),
        lambda: Problem((), (Demand("demand-6", "steel", 1.5, 1),)),
        ValidationErrorCode.INVALID_DEMAND_LENGTH,
        "demand",
        "length",
    ),
    PrimitiveRule(
        "demand quantity is a positive integer",
        lambda: Problem((), (Demand("demand-6", "steel", 6, 1),)),
        lambda: Problem((), (Demand("demand-6", "steel", 6, -1),)),
        ValidationErrorCode.INVALID_DEMAND_QUANTITY,
        "demand",
        "quantity",
    ),
)


@pytest.mark.parametrize("rule", PRIMITIVE_RULES, ids=lambda rule: rule.name)
def test_primitive_validation_rule_matrix(rule: PrimitiveRule) -> None:
    assert validate_problem(rule.valid_problem()) is None

    with pytest.raises(ValidationError) as captured:
        validate_problem(rule.invalid_problem())

    error = captured.value
    assert error.code is rule.code
    assert error.record == rule.record
    assert error.record_index == 0
    assert error.field == rule.field
    assert error.conflicting_ids == ()


@dataclass(frozen=True)
class UniquenessRule:
    name: str
    valid_problem: ProblemFactory
    invalid_problem: ProblemFactory
    code: ValidationErrorCode
    record: str
    field: str
    conflicting_ids: tuple[str, ...]


UNIQUENESS_RULES = (
    UniquenessRule(
        "stock IDs are unique",
        lambda: Problem(
            (
                Stock("stock-a", "steel", 10, 1),
                Stock("stock-b", "steel", 10, 1),
            ),
            (),
        ),
        lambda: Problem(
            (
                Stock("stock-b", "steel", 10, 1),
                Stock("stock-a", "steel", 10, 1),
                Stock("stock-b", "steel", 20, 1),
                Stock("stock-a", "steel", 20, 1),
            ),
            (),
        ),
        ValidationErrorCode.DUPLICATE_STOCK_ID,
        "stock",
        "stock_id",
        ("stock-a", "stock-b"),
    ),
    UniquenessRule(
        "demand IDs are unique",
        lambda: Problem(
            (),
            (
                Demand("demand-a", "steel", 4, 1),
                Demand("demand-b", "steel", 6, 1),
            ),
        ),
        lambda: Problem(
            (),
            (
                Demand("demand-b", "steel", 4, 1),
                Demand("demand-a", "steel", 6, 1),
                Demand("demand-b", "steel", 8, 1),
                Demand("demand-a", "steel", 9, 1),
            ),
        ),
        ValidationErrorCode.DUPLICATE_DEMAND_ID,
        "demand",
        "demand_id",
        ("demand-a", "demand-b"),
    ),
)


@pytest.mark.parametrize("rule", UNIQUENESS_RULES, ids=lambda rule: rule.name)
def test_uniqueness_validation_rule_matrix(rule: UniquenessRule) -> None:
    assert validate_problem(rule.valid_problem()) is None

    with pytest.raises(ValidationError) as captured:
        validate_problem(rule.invalid_problem())

    error = captured.value
    assert error.code is rule.code
    assert error.record == rule.record
    assert error.record_index is None
    assert error.field == rule.field
    assert error.conflicting_ids == rule.conflicting_ids


@pytest.mark.parametrize(
    "problem",
    [
        Problem((), ()),
        Problem((Stock("stock-10", "steel", 10, 1),), ()),
        Problem((), (Demand("demand-6", "steel", 6, 1),)),
        Problem(
            (Stock("shared", "steel", 10, 1),),
            (Demand("shared", "steel", 6, 1),),
        ),
        Problem(
            (Stock("stock-5", "steel", 5, 1),),
            (Demand("demand-6", "steel", 6, 1),),
        ),
        Problem(
            (Stock("stock-steel", "steel", 10, 1),),
            (Demand("demand-aluminum", "aluminum", 6, 1),),
        ),
    ],
    ids=[
        "both collections empty",
        "demand collection empty",
        "stock collection empty",
        "ID namespaces are separate",
        "piece too long is potential infeasibility",
        "missing material is potential infeasibility",
    ],
)
def test_problem_level_valid_input_matrix(problem: Problem) -> None:
    assert validate_problem(problem) is None


def test_v010_unit_is_fixed_to_mm_and_cannot_be_overridden() -> None:
    assert "unit" not in signature(Problem).parameters

    with pytest.raises(TypeError, match="unit"):
        Problem(stocks=(), demands=(), unit="in")  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("problem", "expected_code"),
    [
        (
            Problem(
                (Stock("", "steel", 0, 1),),
                (Demand("", "steel", 0, 1),),
            ),
            ValidationErrorCode.INVALID_STOCK_ID,
        ),
        (
            Problem(
                (
                    Stock("stock-a", "steel", 10, 1),
                    Stock("stock-a", "steel", 10, 1),
                ),
                (Demand("", "steel", 0, 1),),
            ),
            ValidationErrorCode.INVALID_DEMAND_ID,
        ),
        (
            Problem(
                (
                    Stock("stock-a", "steel", 10, 1),
                    Stock("stock-a", "steel", 10, 1),
                ),
                (
                    Demand("demand-a", "steel", 6, 1),
                    Demand("demand-a", "steel", 6, 1),
                ),
            ),
            ValidationErrorCode.DUPLICATE_STOCK_ID,
        ),
    ],
    ids=[
        "first stock field precedes all later errors",
        "demand fields precede collection errors",
        "stock duplicates precede demand duplicates",
    ],
)
def test_multiple_errors_follow_documented_fail_fast_policy(
    problem: Problem,
    expected_code: ValidationErrorCode,
) -> None:
    with pytest.raises(ValidationError) as captured:
        validate_problem(problem)

    assert captured.value.code is expected_code


def test_validation_failure_prevents_solver_invocation() -> None:
    problem = Problem((Stock("stock-10", "steel", 0, 1),), ())
    solver = Mock()

    with pytest.raises(ValidationError):
        validate_problem(problem)
        solver(problem)

    solver.assert_not_called()


def test_solver_can_be_invoked_after_validation_succeeds() -> None:
    problem = Problem((Stock("stock-10", "steel", 10, 1),), ())
    solver = Mock(return_value="solution")

    validate_problem(problem)
    result = solver(problem)

    solver.assert_called_once_with(problem)
    assert result == "solution"
