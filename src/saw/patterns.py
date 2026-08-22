from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

# this somehow prevents circular imports, im not sure how
if TYPE_CHECKING:
    from saw.models import Demand, Problem, Stock


@dataclass(frozen=True, slots=True)
class Pattern:
    stock_id: str
    # the field options make sure piece_counts isnt used in hashing
    piece_counts: Mapping[str, int] = field(compare=False, hash=False)
    # init=False meand this cannot be passed into the constructor
    # repr=False means the field is hidden even when printing the instance
    _normalized_counts: tuple[tuple[str, int], ...] = field(
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        for demand_id, count in self.piece_counts.items():
            if count < 0:
                raise ValueError(f"Negative piece count for demand {demand_id!r}")

        normalized_counts = tuple(sorted(
            (demand_id, count)
            for demand_id, count in self.piece_counts.items()
            if count > 0
        ))
        object.__setattr__(self, "_normalized_counts", normalized_counts)
        object.__setattr__(
            self,
            "piece_counts",
            MappingProxyType(dict(normalized_counts)),
        )

# finds and enumerates all of the possible patterns on a single stock and a demand list
def enumerate_patterns_for_single_stock(
    stock: Stock,
    demands: tuple[Demand, ...],
) -> tuple[Pattern, ...]:
    # makes sure that the stock and demand materials match
    compatible_demands = tuple(
        demand
        for demand in demands
        if demand.material_id == stock.material_id
    )

    patterns: list[Pattern] = []
    counts = [0] * len(compatible_demands)

    # this function traverses the tree in a DFS fashion, finding all of the patterns
    def walk(index: int, remaining_length: int) -> None:
        if index == len(compatible_demands):
            # this if omits empty/ all-zero patterns
            if any(counts):
                piece_counts = {
                    demand.demand_id: count
                    for demand, count in zip(compatible_demands, counts)
                    if count > 0
                }
                patterns.append(Pattern(stock.stock_id, piece_counts))
            return

        demand = compatible_demands[index]
        max_count = min(
            demand.quantity,
            remaining_length // demand.length,
        )

        for count in range(max_count + 1):
            counts[index] = count
            walk(
                index + 1,
                remaining_length - count * demand.length,
            )

        counts[index] = 0

    walk(0, stock.length)
    return tuple(patterns)

# function that just loops over the stocks tuple and calls enumeration for each one
def enumerate_patterns_for_problem(problem: Problem) -> tuple[Pattern, ...]:
    return tuple(
        pattern
        for stock in problem.stocks
        for pattern in enumerate_patterns_for_single_stock(stock, problem.demands)
    )