# saw
A correctness-first, open-source solver for one-dimensional cutting-stock problems, with independently verified solutions, honest optimization guarantees, and reproducible benchmarks.

The authoritative definition of the v0.1.0 problem is in
[docs/problem-spec.md](docs/problem-spec.md).

## Minimal Python example

Construct a problem from stock and demand records, solve it, and check the
status before using its cut patterns:

```python
from saw import Demand, Problem, SolutionStatus, Stock, solve

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
    print(f"{cut_pattern.stock_id} x {cut_pattern.repetitions}: {pieces}")
```

This prints stock and demand IDs from the input:

```text
stock-10 x 1: demand-4 x 1, demand-6 x 1
```

The same code is available as [`examples/minimal.py`](examples/minimal.py)
and can be run from the repository root with:

```shell
python examples/minimal.py
```

## Development

Saw requires Python 3.10 or newer. From the repository root, install the
package and its development dependencies with:

```shell
python -m pip install -e ".[dev]"
```

Run the complete test suite with:

```shell
python -m pytest
```

## Optimization backend dependency

Saw v0.1 supports PuLP versions `>=3.3,<4`. The required `cbc` extra is
declared with that dependency so the adapter can supply PuLP with an
executable CBC solver.
The adapter checks CBC availability at runtime and reports the installed PuLP
version in its availability result.

Public package publishing and release automation are deferred to a later
release.
