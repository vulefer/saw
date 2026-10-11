from itertools import product

from saw import Demand, Problem, Stock
import saw.patterns as patterns_module
from saw.patterns import (
    Pattern,
    enumerate_patterns_for_problem,
    enumerate_patterns_for_single_stock,
)
import saw.timing as timing_module
from saw.timing import Deadline


class FakeClock:
    """A controllable, manually-advanced stand-in for time.perf_counter().

    Deadline-related tests monkeypatch ``patterns_module.perf_counter`` with
    an instance of this so elapsed time is exact and deterministic instead
    of depending on real wall-clock speed (which would make the tests
    flaky). The clock only moves when a test explicitly calls ``advance``.
    """

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


CanonicalPattern = tuple[str, tuple[tuple[str, int], ...]]


def _canonical_pattern_set(patterns: tuple[Pattern, ...]) -> frozenset[CanonicalPattern]:
    return frozenset(
        (pattern.stock_id, tuple(sorted(pattern.piece_counts.items()))) for pattern in patterns
    )


def _independently_generate_pattern_set(
    stocks: tuple[Stock, ...],
    demands: tuple[Demand, ...],
) -> frozenset[CanonicalPattern]:
    patterns: set[CanonicalPattern] = set()

    for stock in stocks:
        compatible_demands = tuple(
            demand for demand in demands if demand.material_id == stock.material_id
        )
        count_ranges = tuple(range(demand.quantity + 1) for demand in compatible_demands)

        for counts in product(*count_ranges):
            if not any(counts):
                continue
            if (
                sum(
                    demand.length * count
                    for demand, count in zip(compatible_demands, counts, strict=True)
                )
                > stock.length
            ):
                continue

            patterns.add(
                (
                    stock.stock_id,
                    tuple(
                        sorted(
                            (demand.demand_id, count)
                            for demand, count in zip(compatible_demands, counts, strict=True)
                            if count > 0
                        )
                    ),
                )
            )

    return frozenset(patterns)


def test_enumerates_every_non_empty_feasible_pattern_for_one_stock() -> None:
    stock = Stock("stock-10", "steel", 10, 2)
    demands = (
        Demand("demand-6", "steel", 6, 1),
        Demand("demand-4", "steel", 4, 1),
        Demand("demand-3-wood", "wood", 3, 2),
    )

    patterns = enumerate_patterns_for_single_stock(stock, demands)

    assert tuple(dict(pattern.piece_counts) for pattern in patterns) == (
        {"demand-4": 1},
        {"demand-6": 1},
        {"demand-4": 1, "demand-6": 1},
    )
    assert all(pattern.stock_id == stock.stock_id for pattern in patterns)


def test_enumerated_patterns_never_exceed_stock_capacity() -> None:
    stock = Stock("stock-10", "steel", 10, 1)
    demands = (
        Demand("demand-6", "steel", 6, 2),
        Demand("demand-4", "steel", 4, 2),
    )
    lengths = {demand.demand_id: demand.length for demand in demands}

    patterns = enumerate_patterns_for_single_stock(stock, demands)

    assert all(
        sum(lengths[demand_id] * count for demand_id, count in pattern.piece_counts.items())
        <= stock.length
        for pattern in patterns
    )
    assert any(dict(pattern.piece_counts) == {"demand-4": 1, "demand-6": 1} for pattern in patterns)


def test_enumerated_counts_never_exceed_demand_quantity() -> None:
    stock = Stock("stock-10", "steel", 10, 1)
    demands = (Demand("demand-2", "steel", 2, 2),)

    patterns = enumerate_patterns_for_single_stock(stock, demands)

    assert tuple(dict(pattern.piece_counts) for pattern in patterns) == (
        {"demand-2": 1},
        {"demand-2": 2},
    )


def test_empty_pattern_is_excluded_when_no_demand_fits() -> None:
    stock = Stock("stock-5", "steel", 5, 1)
    demands = (Demand("demand-6", "steel", 6, 3),)

    patterns = enumerate_patterns_for_single_stock(stock, demands)

    assert patterns == ()


def test_enumeration_is_finite_deterministic_and_has_no_duplicates() -> None:
    stock = Stock("stock-12", "steel", 12, 1)
    demands = (
        Demand("demand-5", "steel", 5, 2),
        Demand("demand-2", "steel", 2, 3),
    )

    first_enumeration = enumerate_patterns_for_single_stock(stock, demands)
    second_enumeration = enumerate_patterns_for_single_stock(stock, demands)

    assert first_enumeration == second_enumeration
    assert len(first_enumeration) == 9
    assert len(set(first_enumeration)) == len(first_enumeration)


def test_enumeration_matches_an_independently_generated_pattern_set() -> None:
    stock = Stock("stock-12", "steel", 12, 1)
    demands = (
        Demand("demand-5", "steel", 5, 2),
        Demand("demand-3", "steel", 3, 3),
        Demand("demand-2", "steel", 2, 4),
    )

    actual = enumerate_patterns_for_single_stock(stock, demands)

    assert _canonical_pattern_set(actual) == _independently_generate_pattern_set((stock,), demands)


def test_stock_ten_includes_the_mixed_six_plus_four_pattern() -> None:
    stock = Stock("stock-10", "steel", 10, 1)
    demands = (
        Demand("demand-6", "steel", 6, 1),
        Demand("demand-4", "steel", 4, 1),
    )

    patterns = enumerate_patterns_for_single_stock(stock, demands)

    assert (
        stock.stock_id,
        (("demand-4", 1), ("demand-6", 1)),
    ) in _canonical_pattern_set(patterns)


def test_oversized_demands_produce_no_corresponding_pieces() -> None:
    stock = Stock("stock-10", "steel", 10, 1)
    demands = (
        Demand("demand-4", "steel", 4, 2),
        Demand("demand-11", "steel", 11, 3),
    )

    patterns = enumerate_patterns_for_single_stock(stock, demands)

    assert patterns
    assert all("demand-11" not in pattern.piece_counts for pattern in patterns)
    assert _canonical_pattern_set(patterns) == _independently_generate_pattern_set(
        (stock,), demands
    )


def test_reordered_demands_produce_the_same_canonical_set() -> None:
    stock = Stock("stock-12", "steel", 12, 1)
    demands = (
        Demand("demand-5", "steel", 5, 2),
        Demand("demand-3", "steel", 3, 3),
        Demand("demand-2", "steel", 2, 4),
    )

    original = enumerate_patterns_for_single_stock(stock, demands)
    reordered = enumerate_patterns_for_single_stock(stock, tuple(reversed(demands)))

    assert _canonical_pattern_set(original) == _canonical_pattern_set(reordered)


def test_multiple_materials_never_produce_mixed_patterns() -> None:
    problem = Problem(
        stocks=(
            Stock("steel-stock", "steel", 10, 1),
            Stock("wood-stock", "wood", 10, 1),
        ),
        demands=(
            Demand("steel-6", "steel", 6, 1),
            Demand("steel-4", "steel", 4, 1),
            Demand("wood-7", "wood", 7, 1),
            Demand("wood-3", "wood", 3, 1),
        ),
    )
    stock_materials = {stock.stock_id: stock.material_id for stock in problem.stocks}
    demand_materials = {demand.demand_id: demand.material_id for demand in problem.demands}

    result = enumerate_patterns_for_problem(problem)

    assert result.limit_reached is False
    assert result.patterns_generated == len(result.patterns)
    assert _canonical_pattern_set(result.patterns) == _independently_generate_pattern_set(
        problem.stocks, problem.demands
    )
    assert all(
        demand_materials[demand_id] == stock_materials[pattern.stock_id]
        for pattern in result.patterns
        for demand_id in pattern.piece_counts
    )


# --- pattern_limit -------------------------------------------------------
#
# The stock/demand pair below is shared by every pattern_limit test: it is
# the same fixture as test_enumeration_is_finite_deterministic_and_has_no_
# duplicates above, where the unbounded enumeration is independently proven
# to contain exactly 9 patterns.


def _nine_pattern_stock_and_demands() -> tuple[Stock, tuple[Demand, ...]]:
    stock = Stock("stock-12", "steel", 12, 1)
    demands = (
        Demand("demand-5", "steel", 5, 2),
        Demand("demand-2", "steel", 2, 3),
    )
    return stock, demands


def test_pattern_limit_none_generates_every_pattern() -> None:
    stock, demands = _nine_pattern_stock_and_demands()
    problem = Problem(stocks=(stock,), demands=demands)

    result = enumerate_patterns_for_problem(problem, pattern_limit=None)

    assert result.limit_reached is False
    assert result.patterns_generated == 9
    assert len(result.patterns) == 9


def test_pattern_limit_exactly_matching_total_is_not_a_hit() -> None:
    stock, demands = _nine_pattern_stock_and_demands()
    problem = Problem(stocks=(stock,), demands=demands)

    result = enumerate_patterns_for_problem(problem, pattern_limit=9)

    assert result.limit_reached is False
    assert result.patterns_generated == 9
    assert len(result.patterns) == 9
    assert _canonical_pattern_set(result.patterns) == _canonical_pattern_set(
        enumerate_patterns_for_single_stock(stock, demands)
    )


def test_pattern_limit_one_under_total_is_a_deterministic_hit() -> None:
    stock, demands = _nine_pattern_stock_and_demands()
    problem = Problem(stocks=(stock,), demands=demands)

    result = enumerate_patterns_for_problem(problem, pattern_limit=8)

    assert result.limit_reached is True
    assert result.patterns_generated == 8
    assert result.patterns == ()


def test_pattern_limit_hit_is_deterministic_across_repeated_calls() -> None:
    stock, demands = _nine_pattern_stock_and_demands()
    problem = Problem(stocks=(stock,), demands=demands)

    first = enumerate_patterns_for_problem(problem, pattern_limit=8)
    second = enumerate_patterns_for_problem(problem, pattern_limit=8)

    assert first == second


def test_pattern_limit_never_returns_a_partial_pattern_set_as_complete() -> None:
    stock, demands = _nine_pattern_stock_and_demands()
    problem = Problem(stocks=(stock,), demands=demands)

    for pattern_limit in range(1, 9):
        result = enumerate_patterns_for_problem(problem, pattern_limit=pattern_limit)

        assert result.limit_reached is True
        assert result.patterns_generated == pattern_limit
        # A hit never exposes the patterns generated before it stopped, so
        # the caller can never mistake a partial set for a complete one.
        assert result.patterns == ()


def test_pattern_limit_is_shared_across_stocks_and_stops_mid_second_stock() -> None:
    # stock-a is a different material, so it is only compatible with
    # demand_a and alone has exactly 3 non-empty patterns (counts 1..3 of
    # that single demand piece).
    stock_a = Stock("stock-a", "wood", 9, 1)
    demand_a = Demand("demand-3-wood", "wood", 3, 3)
    stock_b, demands_b = _nine_pattern_stock_and_demands()

    problem = Problem(
        stocks=(stock_a, stock_b),
        demands=(demand_a, *demands_b),
    )

    unbounded_a = enumerate_patterns_for_single_stock(
        stock_a,
        (demand_a, *demands_b),
    )
    assert len(unbounded_a) == 3

    unbounded_full = enumerate_patterns_for_problem(problem)
    assert unbounded_full.patterns_generated == 12
    assert unbounded_full.limit_reached is False

    # 3 patterns from stock-a plus 4 patterns into stock-b's own 9.
    result = enumerate_patterns_for_problem(problem, pattern_limit=7)

    assert result.limit_reached is True
    assert result.patterns_generated == 7
    assert result.patterns == ()


def test_pattern_limit_checks_the_budget_while_generating_not_only_after_completion(
    monkeypatch,
) -> None:
    """Enumeration must stop mid-stock, not just between stocks.

    This wraps the internal per-stock generator with a counting proxy and
    asserts that fewer patterns were ever pulled from it than the stock's
    own (independently known) total of 9 -- proving the shared budget is
    consulted pattern-by-pattern during a single stock's own DFS walk, not
    only once that stock's full set has already been built.
    """

    stock, demands = _nine_pattern_stock_and_demands()
    problem = Problem(stocks=(stock,), demands=demands)

    pulled: list[Pattern] = []
    real_iter = patterns_module._iter_patterns_for_single_stock

    def counting_iter(stock_arg, demands_arg):
        for pattern in real_iter(stock_arg, demands_arg):
            pulled.append(pattern)
            yield pattern

    monkeypatch.setattr(
        patterns_module,
        "_iter_patterns_for_single_stock",
        counting_iter,
    )

    result = enumerate_patterns_for_problem(problem, pattern_limit=5)

    assert result.limit_reached is True
    assert result.patterns_generated == 5
    # Only the 5 accepted patterns plus the 6th one that revealed the limit
    # were ever pulled -- the remaining 3 of the stock's 9 patterns were
    # never computed.
    assert len(pulled) == 6


# --- deadline (shared end-to-end time budget) -----------------------------
#
# These reuse the same 9-pattern fixture as the pattern_limit tests above,
# and a FakeClock instead of real sleeping so the deadline can be made to
# expire at an exact, deterministic point (see docs/resource-limits.md,
# "One end-to-end time budget", and "During enumeration Saw checks the time
# deadline before checking whether the next pattern would exceed the
# pattern limit").


def test_no_deadline_generates_every_pattern_regardless_of_pattern_limit() -> None:
    stock, demands = _nine_pattern_stock_and_demands()
    problem = Problem(stocks=(stock,), demands=demands)

    result = enumerate_patterns_for_problem(problem, deadline=None)

    assert result.deadline_expired is False
    assert result.limit_reached is False
    assert result.patterns_generated == 9


def test_deadline_expired_before_enumeration_starts_is_reported_immediately(
    monkeypatch,
) -> None:
    stock, demands = _nine_pattern_stock_and_demands()
    problem = Problem(stocks=(stock,), demands=demands)
    clock = FakeClock(start=0.0)
    monkeypatch.setattr(timing_module, "perf_counter", clock)
    deadline = Deadline.from_start(started_at=0.0, time_limit_seconds=1.0)
    clock.advance(2.0)  # the deadline (at 1.0) has already passed

    result = enumerate_patterns_for_problem(problem, deadline=deadline)

    assert result.deadline_expired is True
    assert result.limit_reached is False
    assert result.patterns_generated == 0
    assert result.patterns == ()


def test_deadline_expiring_mid_enumeration_stops_generation_and_discards_patterns(
    monkeypatch,
) -> None:
    stock, demands = _nine_pattern_stock_and_demands()
    problem = Problem(stocks=(stock,), demands=demands)
    clock = FakeClock(start=0.0)
    monkeypatch.setattr(timing_module, "perf_counter", clock)

    pulled: list[Pattern] = []
    real_iter = patterns_module._iter_patterns_for_single_stock

    def counting_iter(stock_arg, demands_arg):
        for index, pattern in enumerate(real_iter(stock_arg, demands_arg)):
            pulled.append(pattern)
            if index == 5:
                # The deadline passes only once 5 patterns have already
                # been accepted (indices 0-4), proving the check happens
                # mid-stock rather than only between stocks.
                clock.advance(10.0)
            yield pattern

    monkeypatch.setattr(
        patterns_module,
        "_iter_patterns_for_single_stock",
        counting_iter,
    )
    deadline = Deadline.from_start(started_at=0.0, time_limit_seconds=1.0)

    result = enumerate_patterns_for_problem(problem, deadline=deadline)

    assert result.deadline_expired is True
    assert result.limit_reached is False
    # 5 patterns were already accepted before the 6th pull revealed the
    # expired deadline; that 6th one is never appended.
    assert result.patterns_generated == 5
    assert result.patterns == ()
    assert len(pulled) == 6


def test_deadline_is_checked_before_pattern_limit_when_both_would_trigger(
    monkeypatch,
) -> None:
    stock, demands = _nine_pattern_stock_and_demands()
    problem = Problem(stocks=(stock,), demands=demands)
    clock = FakeClock(start=0.0)
    monkeypatch.setattr(timing_module, "perf_counter", clock)
    deadline = Deadline.from_start(started_at=0.0, time_limit_seconds=1.0)
    clock.advance(2.0)  # already expired before the very first pattern

    # pattern_limit=0 would also trip on the very first pattern; the
    # deadline check must win per docs/resource-limits.md.
    result = enumerate_patterns_for_problem(
        problem,
        pattern_limit=0,
        deadline=deadline,
    )

    assert result.deadline_expired is True
    assert result.limit_reached is False


def test_deadline_reached_but_not_exceeded_after_completion_does_not_discard_results(
    monkeypatch,
) -> None:
    """A deadline that is only checked before generating the *next* pattern
    must not retroactively invalidate a complete enumeration that finished
    exactly as the clock ran out (docs/resource-limits.md: "A configured
    threshold that was reached but not exceeded does not replace a
    completed mathematical result with a limit result.").
    """
    stock, demands = _nine_pattern_stock_and_demands()
    problem = Problem(stocks=(stock,), demands=demands)
    clock = FakeClock(start=0.0)
    monkeypatch.setattr(timing_module, "perf_counter", clock)
    deadline = Deadline.from_start(started_at=0.0, time_limit_seconds=1.0)

    result = enumerate_patterns_for_problem(problem, deadline=deadline)

    assert result.deadline_expired is False
    assert result.limit_reached is False
    assert result.patterns_generated == 9
