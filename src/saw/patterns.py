from __future__ import annotations

from collections.abc import Iterator, Mapping
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


# result of enumerating patterns for a whole problem under an optional
# pattern_limit budget.
@dataclass(frozen=True, slots=True)
class PatternEnumerationResult:
    """Outcome of a (possibly budget-limited) pattern enumeration pass.

    When ``limit_reached`` is ``False``, ``patterns`` holds every complete,
    non-empty pattern for the problem and ``patterns_generated`` equals
    ``len(patterns)``.

    When ``limit_reached`` is ``True``, enumeration stopped the moment
    generating the next pattern would have exceeded ``pattern_limit``.
    ``patterns_generated`` is then exactly the configured limit, and
    ``patterns`` is always empty: per docs/resource-limits.md a pattern-limit
    hit discards the partial enumeration rather than exposing it as if it
    were a complete pattern set.
    """

    patterns: tuple[Pattern, ...]
    patterns_generated: int
    limit_reached: bool


# lazily walks the DFS tree for a single stock, yielding one pattern at a
# time instead of materializing the whole set. This is what lets a shared
# pattern_limit budget stop mid-stock, before a single stock's (possibly
# exponential) pattern set is ever fully built in memory.
def _iter_patterns_for_single_stock(
    stock: Stock,
    demands: tuple[Demand, ...],
) -> Iterator[Pattern]:
    # makes sure that the stock and demand materials match
    compatible_demands = tuple(
        demand
        for demand in demands
        if demand.material_id == stock.material_id
    )

    counts = [0] * len(compatible_demands)

    # this function traverses the tree in a DFS fashion, yielding every
    # pattern as soon as it is found rather than collecting them first
    def walk(index: int, remaining_length: int) -> Iterator[Pattern]:
        if index == len(compatible_demands):
            # this if omits empty/ all-zero patterns
            if any(counts):
                piece_counts = {
                    demand.demand_id: count
                    for demand, count in zip(compatible_demands, counts)
                    if count > 0
                }
                yield Pattern(stock.stock_id, piece_counts)
            return

        demand = compatible_demands[index]
        max_count = min(
            demand.quantity,
            remaining_length // demand.length,
        )

        for count in range(max_count + 1):
            counts[index] = count
            yield from walk(
                index + 1,
                remaining_length - count * demand.length,
            )

        counts[index] = 0

    yield from walk(0, stock.length)


# finds and enumerates all of the possible patterns on a single stock and a demand list
def enumerate_patterns_for_single_stock(
    stock: Stock,
    demands: tuple[Demand, ...],
) -> tuple[Pattern, ...]:
    return tuple(_iter_patterns_for_single_stock(stock, demands))


# loops over the stocks tuple and enumerates patterns for each one, in
# order, stopping as soon as generating the next pattern would exceed
# pattern_limit. The budget is shared across every stock record, matching
# the pattern_limit row of docs/resource-limits.md ("Complete, non-empty
# patterns generated across all stock records").
def enumerate_patterns_for_problem(
    problem: Problem,
    pattern_limit: int | None = None,
) -> PatternEnumerationResult:
    generated: list[Pattern] = []

    for stock in problem.stocks:
        for pattern in _iter_patterns_for_single_stock(stock, problem.demands):
            # Checked before appending: when exactly `pattern_limit`
            # patterns have already been generated, attempting this next
            # (limit + 1)th one is what trips the limit. Reaching exactly
            # pattern_limit and then running out of patterns on its own
            # never enters this branch, so that case is allowed, per
            # docs/resource-limits.md.
            if pattern_limit is not None and len(generated) >= pattern_limit:
                return PatternEnumerationResult(
                    patterns=(),
                    patterns_generated=pattern_limit,
                    limit_reached=True,
                )
            generated.append(pattern)

    return PatternEnumerationResult(
        patterns=tuple(generated),
        patterns_generated=len(generated),
        limit_reached=False,
    )
