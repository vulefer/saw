from saw import Demand, Stock
from saw.patterns import enumerate_patterns_for_single_stock


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
