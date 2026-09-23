# saw
A correctness-first, open-source solver for one-dimensional cutting-stock problems, with independently verified solutions, honest optimization guarantees, and reproducible benchmarks.

The authoritative definition of the v0.1.0 problem is in
[docs/problem-spec.md](docs/problem-spec.md).

The independently versioned JSON problem, solution, and error formats are
defined in [docs/formats.md](docs/formats.md). Their version 1 contracts are
the reference for the parser, the serializer, and the command-line interface.
The solution contract makes plan presence status-dependent, so an omitted plan
cannot be mistaken for an empty or verified plan. The `saw` command's usage,
outcome, exit-code, and standard-stream rules are defined in
[docs/cli.md](docs/cli.md).

Before relying on a result, read the v0.1.0
[guarantees](docs/guarantees.md) and
[limitations](docs/limitations.md). In particular, a verified plan is not by
itself proof that the plan is optimal, and the exact backend is intended only
for small and medium-small instances.

Saw v0.2.0's planned time, problem-size, numeric-magnitude, and pattern-count
budgets have one authoritative [resource-limit contract](docs/resource-limits.md).
It defines their units, safe defaults, override policy, end-to-end timing, and
truthful outcomes with and without a verified incumbent.

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

## Command-line example

Installing the package provides the `saw` command. It reads a version 1 JSON
problem from a file or standard input and writes exactly one JSON document --
a solution or an error envelope -- to standard output:

```shell
python -m pip install .
saw solve examples/json/problem-v1-valid.json
```

The exit code is `0` for a verified `optimal` or `feasible` plan and non-zero
for every other outcome (for example `2` for invalid input, `3` for proved
infeasibility, `4` for a reached resource limit); see
[docs/cli.md](docs/cli.md) for the full table and all options.

## Compatibility

Saw v0.2.0 supports standard, GIL-enabled CPython 3.10 through 3.14 on
64-bit Linux, Windows, and Intel macOS systems within the tested platform
scope. Python 3.15 and later are not considered supported until they are
added to the test matrix.

The complete platform and unsupported-environment policy is documented in
[docs/compatibility.md](docs/compatibility.md).

## Development

Use a supported CPython version. From the repository root, install the package
and its development dependencies with:

```shell
python -m pip install -e ".[dev]"
```

Run every test required for a release (unit, regression, oracle, and the
documented example) with this single command from the repository root:

```shell
python scripts/run_release_tests.py
```

The command exits with a non-zero status if any required test fails.

## Optimization backend dependencies

Saw v0.2 supports PuLP versions `>=3.3.2,<4` and cbcbox versions
`>=2.929,<3`. Both are direct dependencies: PuLP provides the modeling API,
and cbcbox provides the CBC executable used by the default backend.
The adapter checks CBC availability at runtime and reports the installed PuLP
version in its availability result.

Public package publishing and release automation are deferred to a later
release.
