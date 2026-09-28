"""The exact backend declines problems its CBC model file cannot represent.

PuLP writes the model for CBC with 13 significant digits, so an integer
above that range reaches CBC already rounded and the solved objective no
longer matches the plan. Such a problem must be reported as ``unsupported``
before solving -- never solved into a result that fails verification.
"""

from __future__ import annotations

import pytest

from saw import Demand, Problem, SolutionStatus, SolveOptions, Stock, solve
from saw.backends.base import BackendOptions
from saw.backends.exact_enumerated.backend import (
    MAX_EXACT_MODEL_VALUE,
    ExactEnumeratedBackend,
)
from saw.models import TerminationReason

_NO_MAGNITUDE_LIMIT = SolveOptions(numeric_magnitude_limit=None)


def _problem(
    *,
    stock_length: int,
    stock_quantity: int = 1,
    demand_quantity: int = 1,
) -> Problem:
    return Problem(
        stocks=(Stock("stock", "steel", stock_length, stock_quantity),),
        demands=(Demand("piece", "steel", 1, demand_quantity),),
    )


def _support(problem: Problem):
    return ExactEnumeratedBackend().check_support(problem, BackendOptions())


def test_a_total_stock_length_at_the_bound_is_supported() -> None:
    assert _support(_problem(stock_length=MAX_EXACT_MODEL_VALUE)).supported


@pytest.mark.parametrize(
    "problem",
    [
        _problem(stock_length=MAX_EXACT_MODEL_VALUE + 1),
        # Each value fits alone; the total stock length does not.
        _problem(stock_length=MAX_EXACT_MODEL_VALUE // 2 + 1, stock_quantity=2),
        _problem(stock_length=10, demand_quantity=MAX_EXACT_MODEL_VALUE + 1),
    ],
    ids=["stock-length", "total-stock-length", "demand-quantity"],
)
def test_a_value_beyond_the_bound_is_unsupported_with_a_reason(problem: Problem) -> None:
    support = _support(problem)

    assert not support.supported
    assert str(MAX_EXACT_MODEL_VALUE) in support.reason


def test_the_largest_supported_problem_solves_to_a_verified_optimum() -> None:
    # A leftover of 10**12 - 1 needs all 12 digits to reach CBC intact.
    solution = solve(_problem(stock_length=MAX_EXACT_MODEL_VALUE), _NO_MAGNITUDE_LIMIT)

    assert solution.solution_status is SolutionStatus.OPTIMAL
    assert solution.objective_value.total_leftover == MAX_EXACT_MODEL_VALUE - 1
    assert solution.verification_report.is_valid


@pytest.mark.parametrize("stock_length", [2**64, 10**22, 10**30])
def test_out_of_range_lengths_are_unsupported_not_an_internal_error(
    stock_length: int,
) -> None:
    # These used to reach CBC rounded and fail verification (exit 70).
    solution = solve(_problem(stock_length=stock_length), _NO_MAGNITUDE_LIMIT)

    assert solution.solution_status is SolutionStatus.UNSUPPORTED
    assert solution.diagnostics.termination_reason is TerminationReason.UNSUPPORTED_PROBLEM
    assert solution.cut_patterns == ()
