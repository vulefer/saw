# saw
A correctness-first, open-source solver for one-dimensional cutting-stock problems, with independently verified solutions, honest optimization guarantees, and reproducible benchmarks.

The authoritative definition of the v0.1.0 problem is in
[docs/problem-spec.md](docs/problem-spec.md).

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

Public package publishing and release automation are deferred to a later
release.
