from itertools import product

from saw import Demand, Problem, Stock
from saw.patterns import (
    Pattern,
    enumerate_patterns_for_problem,
    enumerate_patterns_for_single_stock,
)


CanonicalPattern = tuple[str, tuple[tuple[str, int], ...]]


def _canonical_pattern_set(patterns: tuple[Pattern, ...]) -> frozenset[CanonicalPattern]:
    return frozenset(
        (pattern.stock_id, tuple(sorted(pattern.piece_counts.items())))
        for pattern in patterns
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
        count_ranges = tuple(
            range(demand.quantity + 1) for demand in compatible_demands
        )

        for counts in product(*count_ranges):
            if not any(counts):
                continue
            if sum(
                demand.length * count
                for demand, count in zip(compatible_demands, counts)
            ) > stock.length:
                continue

            patterns.add((
                stock.stock_id,
                tuple(sorted(
                    (demand.demand_id, count)
                    for demand, count in zip(compatible_demands, counts)
                    if count > 0
                )),
            ))

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
    assert any(
        dict(pattern.piece_counts) == {"demand-4": 1, "demand-6": 1}
        for pattern in patterns
    )


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

    assert _canonical_pattern_set(actual) == _independently_generate_pattern_set(
        (stock,), demands
    )


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
    stock_materials = {
        stock.stock_id: stock.material_id for stock in problem.stocks
    }
    demand_materials = {
        demand.demand_id: demand.material_id for demand in problem.demands
    }

    patterns = enumerate_patterns_for_problem(problem)

    assert _canonical_pattern_set(patterns) == _independently_generate_pattern_set(
        problem.stocks, problem.demands
    )
    assert all(
        demand_materials[demand_id] == stock_materials[pattern.stock_id]
        for pattern in patterns
        for demand_id in pattern.piece_counts
    )
