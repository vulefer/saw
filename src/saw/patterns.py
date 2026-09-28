from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

from saw.timing import Deadline

# imported only for type hints, this prevents circular imports by never running
if TYPE_CHECKING:
    from saw.models import Demand, Problem, Stock


@dataclass(frozen=True, slots=True)
class Pattern:
    stock_id: str
    # piece_counts is skipped in == and hashing, _normalized_counts is used instead
    piece_counts: Mapping[str, int] = field(compare=False, hash=False)
    # init=False means this cannot be passed into the constructor
    # repr=False means the field is hidden when printing the instance
    _normalized_counts: tuple[tuple[str, int], ...] = field(
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        for demand_id, count in self.piece_counts.items():
            if count < 0:
                raise ValueError(f"Negative piece count for demand {demand_id!r}")

        normalized_counts = tuple(
            sorted(
                (demand_id, count) for demand_id, count in self.piece_counts.items() if count > 0
            )
        )
        object.__setattr__(self, "_normalized_counts", normalized_counts)
        object.__setattr__(
            self,
            "piece_counts",
            MappingProxyType(dict(normalized_counts)),
        )


# outcome of enumerating patterns for a whole problem
# if pattern_limit or the deadline is hit, patterns is empty, not a partial list
@dataclass(frozen=True, slots=True)
class PatternEnumerationResult:
    patterns: tuple[Pattern, ...]
    patterns_generated: int
    limit_reached: bool
    deadline_expired: bool = False


# lazily walks the DFS tree for a single stock, yielding one pattern at a time
# instead of building the whole set, so the caller can stop early on a limit
def _iter_patterns_for_single_stock(
    stock: Stock,
    demands: tuple[Demand, ...],
) -> Iterator[Pattern]:
    # keeps only the demands with the same material as the stock
    compatible_demands = tuple(
        demand for demand in demands if demand.material_id == stock.material_id
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
                    for demand, count in zip(compatible_demands, counts, strict=True)
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


# enumerates patterns for every stock in order, stopping early if the deadline
# passes or the next pattern would go over pattern_limit
# pattern_limit counts patterns across all stocks, see docs/resource-limits.md
# no deadline passed means no time limit
def enumerate_patterns_for_problem(
    problem: Problem,
    pattern_limit: int | None = None,
    *,
    deadline: Deadline | None = None,
) -> PatternEnumerationResult:
    generated: list[Pattern] = []
    effective_deadline = deadline if deadline is not None else Deadline(None)

    for stock in problem.stocks:
        for pattern in _iter_patterns_for_single_stock(stock, problem.demands):
            # deadline is checked first, so if both limits are hit it counts
            # as a time limit, see docs/resource-limits.md
            if effective_deadline.expired():
                return PatternEnumerationResult(
                    patterns=(),
                    patterns_generated=len(generated),
                    limit_reached=False,
                    deadline_expired=True,
                )
            # checked before appending, so exactly pattern_limit patterns is
            # allowed, only trying to add one more hits the limit
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
