# Saw dependency policy

This document defines which packages Saw may depend on, how their version
bounds are chosen, and how the checked-in locks reproduce the development and
CI environments.

## Runtime dependencies

The core package has exactly two runtime dependencies:

| Package | Bounds | Why Saw needs it |
| --- | --- | --- |
| PuLP | `>=3.3.2,<4` | Builds the integer programs and drives the solver. 3.3.2 is the first release that locates CBC through cbcbox. |
| cbcbox | `>=2.929,<3` | Ships the CBC executable that the PuLP backend runs. |

Everything else Saw does (validation, serialization, verification, the CLI)
uses only the Python standard library.

No web framework, database driver, ORM, authentication library, HTTP client,
or other service dependency may enter the core package. A feature that needs
one belongs in a separate package or an optional extra that the core never
imports. This is enforced by `tests/compatibility/test_dependencies.py`, which
fails if `pyproject.toml` declares any other runtime dependency or if any
module under `src/saw` imports a third-party package other than PuLP and
cbcbox.

## Development dependencies

The `dev` extra holds what the release suite and the artifact build need
and nothing else:

| Package | Bounds | Why the suite needs it |
| --- | --- | --- |
| pytest | `>=9,<10` | Runs the release suite. |
| jsonschema | `>=4.23,<5` | Checks JSON output against the draft 2020-12 schemas in `src/saw/schemas/`. |
| packaging | `>=24.2,<27` | Parses requirements and versions in the dependency policy tests and validates built metadata; 24.2 is the first release that reads metadata 2.4 (`License-Expression`, `License-File`). |
| tomli | `>=2.0.1,<3` (Python 3.10 only) | Reads `pyproject.toml` where `tomllib` is not available. |
| build | `>=1.2.1,<2` | Builds the sdist and wheel in `scripts/build_distributions.py`. |
| ruff | `>=0.16.9,<0.17` | Formats and lints in `scripts/run_static_checks.py`. Ruff changes its formatter style and rules between 0.x minor releases, so the upper bound is the next minor. |
| mypy | `>=2.3.1,<3` | Type checks `src/saw`, `scripts/` and `examples/` in `scripts/run_static_checks.py`. |

The library never imports these packages.

## How bounds are chosen

Every direct dependency, runtime or development, carries both a lower and an
upper bound:

- The **lower bound** is the oldest release the minimum-dependencies CI job
  tests. It is raised only when Saw needs a newer feature or fix, and the
  reason is recorded next to the requirement in `pyproject.toml` or in the
  tables above.
- The **upper bound** excludes the next major release, where upstream may
  make breaking changes. It is raised only after the release suite passes on
  the new major version.

`tests/compatibility/test_dependencies.py` checks that every direct
dependency has both bounds.

## Locks

Two constraint files under `requirements/` pin every package, direct and
transitive, for all supported operating systems and Python 3.10 to 3.14:

| File | Contents | Used by |
| --- | --- | --- |
| `requirements/dev.txt` | The newest versions the bounds allow at the time of locking. | Local development, the static checks, the build, and the compatibility matrix. |
| `requirements/minimum.txt` | Every direct dependency at its lower bound. | The minimum-dependencies job. |

Both are generated from `pyproject.toml` by `scripts/lock_dependencies.py`
and are never edited by hand. The release suite checks that the dev lock pins
every direct dependency inside its bounds and that the minimum lock pins each
one at exactly its lower bound.

The locks are constraints, not requirement lists: they restrict versions but
do not install anything by themselves. Install Saw and its development
dependencies exactly as CI does with:

```shell
python -m pip install --constraint requirements/dev.txt --editable ".[dev]"
```

To reproduce the minimum-dependencies job instead, use Python 3.10 and
`requirements/minimum.txt`.

The `latest-dependencies` CI job deliberately ignores the locks and installs
the newest versions the bounds allow, so an upstream release that breaks Saw
is noticed before the lock is regenerated.

## Updating dependencies

The locks are generated with [uv](https://docs.astral.sh/uv/), which is a
maintainer tool and not part of the `dev` extra. Install it into the
environment you run the script from:

```shell
python -m pip install uv
```

To pick up new upstream releases within the current bounds:

1. Run `python scripts/lock_dependencies.py --upgrade`. Without `--upgrade`,
   uv keeps the versions already in `requirements/dev.txt` and only changes
   what `pyproject.toml` forces it to. To update a single package, use
   `--upgrade-package <name>` instead.
2. Reinstall with the dev lock and run `python scripts/run_release_tests.py`.
3. Commit `requirements/dev.txt` and `requirements/minimum.txt` together.

To add a dependency or change a bound:

1. Edit `pyproject.toml` and record why the bound is what it is.
2. For a runtime dependency, first confirm it is allowed by the rules above,
   then update `ALLOWED_RUNTIME_DEPENDENCIES` in
   `tests/compatibility/test_dependencies.py` and the runtime table in this
   document. For a new third-party import in `src/saw`, also update
   `ALLOWED_THIRD_PARTY_IMPORTS`.
3. Run `python scripts/lock_dependencies.py` and the release suite against
   both locks.
4. If a runtime bound changed, update `docs/compatibility.md` and the README
   in the same pull request.
