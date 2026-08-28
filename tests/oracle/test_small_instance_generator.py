from saw import validate_problem

from tests.oracle import generate_small_instances, solve_small_instance


def test_generation_is_exhaustive_bounded_and_deterministic() -> None:
    first = generate_small_instances()
    second = generate_small_instances()

    assert first == second
    assert len(first) == 81
    assert len(set(first)) == len(first)


def test_every_generated_problem_is_valid() -> None:
    for problem in generate_small_instances():
        assert validate_problem(problem) is None


def test_generated_records_vary_lengths_and_positive_quantities() -> None:
    problems = generate_small_instances()

    assert {
        stock.length for problem in problems for stock in problem.stocks
    } == {3, 5}
    assert {
        stock.quantity for problem in problems for stock in problem.stocks
    } == {1, 2}
    assert {
        demand.length for problem in problems for demand in problem.demands
    } == {2, 4}
    assert {
        demand.quantity for problem in problems for demand in problem.demands
    } == {1, 2}


def test_some_generated_problems_have_multiple_stock_lengths() -> None:
    problems = generate_small_instances()

    assert any(len(problem.stocks) > 1 for problem in problems)


def test_generated_set_contains_feasible_and_infeasible_problems() -> None:
    outcomes = tuple(
        (problem, solve_small_instance(problem))
        for problem in generate_small_instances()
    )

    assert any(
        problem.demands and result.feasible
        for problem, result in outcomes
    )
    assert any(
        problem.stocks and problem.demands and not result.feasible
        for problem, result in outcomes
    )
