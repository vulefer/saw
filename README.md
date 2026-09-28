# saw
A correctness-first solver for one-dimensional cutting-stock problems, with independently verified solutions, honest optimization guarantees, and reproducible benchmarks.

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
and its development dependencies, pinned to the same versions CI uses, with:

```shell
python -m pip install --constraint requirements/dev.txt -e ".[dev]"
```

The locks under `requirements/` and how to update them are described in
[docs/dependencies.md](docs/dependencies.md).

Run every test required for a release (unit, regression, oracle, and the
documented example) with this single command from the repository root:

```shell
python scripts/run_release_tests.py
```

The command exits with a non-zero status if any required test fails.

## Building release artifacts

From a clean checkout with the development dependencies installed, build the
source distribution and the wheel into `dist/` and check both with:

```shell
python scripts/build_distributions.py
```

The wheel is built from the sdist, so a file missing from the sdist fails the
build. The script then validates the core metadata of both artifacts against
`pyproject.toml`, checks that the wheel carries the `saw` console entry point,
`py.typed` and the versioned JSON schemas from `src/saw/schemas/` with a
matching `RECORD`, checks that the sdist holds exactly the tracked release
files, and fails if the build left any change in the checkout other than
ignored outputs (`dist/`, `build/`, `*.egg-info`). It refuses to run on a
checkout with uncommitted changes unless `--allow-dirty` is passed.
Timestamps in the wheel are pinned to the last commit, so the same commit and
toolchain produce a byte-identical wheel.

To test the wheel users actually receive rather than an editable install, run:

```shell
python scripts/smoke_test_wheel.py
```

It creates a fresh virtual environment in a temporary directory outside the
checkout and installs only the wheel from `dist/` (no extras, runtime
dependencies pinned by `requirements/dev.txt`, change with `--constraint`).
It then imports every `saw` module from that environment, so an undeclared
runtime dependency fails even in a module the examples never reach, checks
`py.typed` and the packaged JSON schemas against the source tree, and runs the
Python example, `saw --help`, `saw --version`, and `saw solve` on the
documented problem, validating its output with the schema loaded from the
installed wheel. Every failure names the gate that broke. It needs network
access to install PuLP and cbcbox and takes about half a minute.

## Optimization backend dependencies

Saw v0.2 supports PuLP versions `>=3.3.2,<4` and cbcbox versions
`>=2.929,<3`. Both are direct dependencies: PuLP provides the modeling API,
and cbcbox provides the CBC executable used by the default backend.
The adapter checks CBC availability at runtime and reports the installed PuLP
version in its availability result. No other runtime dependency is allowed
into the core package; see [docs/dependencies.md](docs/dependencies.md).

Public package publishing and release automation are deferred to a later
release.
