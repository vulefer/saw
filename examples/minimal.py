"""Solve and print a minimal one-dimensional cutting-stock problem."""

from saw import Demand, Problem, SolutionStatus, Stock, solve


def main() -> None:
    problem = Problem(
        stocks=(Stock("stock-10", "steel", length=10, quantity=1),),
        demands=(
            Demand("demand-6", "steel", length=6, quantity=1),
            Demand("demand-4", "steel", length=4, quantity=1),
        ),
    )

    solution = solve(problem)
    if solution.solution_status is not SolutionStatus.OPTIMAL:
        raise RuntimeError(
            f"Expected an optimal solution, got {solution.solution_status.value}"
        )

    for cut_pattern in solution.cut_patterns:
        pieces = ", ".join(
            f"{demand_id} x {count}"
            for demand_id, count in cut_pattern.piece_counts.items()
        )
        print(
            f"{cut_pattern.stock_id} x {cut_pattern.repetitions}: {pieces}"
        )


if __name__ == "__main__":
    main()
