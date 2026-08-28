"""Deterministically generate an exhaustive, bounded set of tiny problems.

This module is test-only.  Each supported length is either absent or has a
quantity of one or two, so the Cartesian product covers every problem in the
declared tiny domain while remaining small enough for normal test runs.
"""

from __future__ import annotations

from itertools import product

from saw.models import Demand, Problem, Stock


_MATERIAL_ID = "steel"
_STOCK_LENGTHS = (3, 5)
_DEMAND_LENGTHS = (2, 4)
_QUANTITIES = (0, 1, 2)


def generate_small_instances() -> tuple[Problem, ...]:
    """Return every valid problem in the generator's finite tiny domain.

    A zero in an internal quantity tuple means that the corresponding record
    is absent; zero is never emitted as a record quantity.  Consequently the
    generated problems include valid empty collections as well as one- and
    two-length stock and demand collections.
    """

    quantity_vectors = tuple(product(_QUANTITIES, repeat=2))
    problems: list[Problem] = []

    for stock_quantities in quantity_vectors:
        stocks = tuple(
            Stock(
                stock_id=f"stock-{length}",
                material_id=_MATERIAL_ID,
                length=length,
                quantity=quantity,
            )
            for length, quantity in zip(
                _STOCK_LENGTHS,
                stock_quantities,
                strict=True,
            )
            if quantity > 0
        )

        for demand_quantities in quantity_vectors:
            demands = tuple(
                Demand(
                    demand_id=f"demand-{length}",
                    material_id=_MATERIAL_ID,
                    length=length,
                    quantity=quantity,
                )
                for length, quantity in zip(
                    _DEMAND_LENGTHS,
                    demand_quantities,
                    strict=True,
                )
                if quantity > 0
            )
            problems.append(Problem(stocks=stocks, demands=demands))

    return tuple(problems)
