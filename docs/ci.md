# Continuous integration

The GitHub Actions workflow in `.github/workflows/ci.yml` runs the
correctness and packaging gates of the v0.2.0 release on the matrix that
[compatibility.md](compatibility.md) declares supported. Every job calls the
same scripts a maintainer runs locally, so a CI failure can always be
reproduced with one command.

## Matrix strategy

| Job | Operating systems | CPython | Dependencies | Why |
| --- | --- | --- | --- | --- |
| `static-checks` | Ubuntu 24.04 | 3.14 | `requirements/dev.txt` | Formatting, lint, and types do not depend on the platform, and mypy checks against Python 3.10 (`pyproject.toml`) whichever interpreter runs it. |
| `build` | Ubuntu 24.04 | 3.14 | `requirements/dev.txt` | Saw is pure Python, so one `py3-none-any` wheel serves every platform. It is built once and every later job tests that exact file. |
| `compatibility` | Ubuntu 24.04, Windows Server 2025, macOS 15 Intel | 3.10, 3.11, 3.12, 3.13, 3.14 | `requirements/dev.txt` | The full cross product, because the compatibility policy promises every cell and none of them is allowed to be inferred from another. |
| `minimum-dependencies` | all three | 3.10 | `requirements/minimum.txt` | The oldest dependency releases Saw declares, on the oldest supported Python, on every operating system, where binary CBC differences show up. |
| `latest-dependencies` | Ubuntu 24.04 | 3.10, 3.14 | none, newest allowed | Catches an upstream release inside the declared bounds before the lock is regenerated. The oldest and newest Python bracket the range. |

`fail-fast` is off everywhere, so one broken cell never hides the result of
another.

## What every test job does

After `build`, each job:

1. downloads the wheel and installs it with the `dev` extra under its lock,
   never the checkout and never an editable install;
2. runs `scripts/report_solver.py`, which prints the interpreter, the saw,
   PuLP, and cbcbox versions, the CBC executable Saw will use, and its build
   banner, and fails the job if CBC is not usable;
3. runs the release suite, `scripts/run_release_tests.py`, against the
   installed wheel;
4. runs `scripts/smoke_test_wheel.py`, which installs the same wheel alone
   into a fresh virtual environment under the job's runtime lock.

## Gate coverage

| Gate | Where it runs |
| --- | --- |
| Unit tests | `tests/unit` in the release suite, every test job |
| Regression tests | `tests/regression` in the release suite, every test job |
| Oracle tests | `tests/oracle` in the release suite, every test job |
| Serialization and JSON schema contracts | `tests/contracts` and `tests/unit/test_serialization.py` in the release suite, every test job |
| CLI | `tests/unit/test_cli*.py` and the installed `saw` command in `tests/integration`, every test job; `saw --help`, `--version` and `solve` again in the smoke test |
| Supported environment and CBC | `tests/compatibility` in the release suite and `scripts/report_solver.py`, every test job |
| Static checks | `scripts/run_static_checks.py`, `static-checks` job |
| Build | `scripts/build_distributions.py`, `build` job |
| Clean install | `scripts/smoke_test_wheel.py`, every test job |

## No silent skips

The release suite has no optional tests. `tests/conftest.py` turns any
skipped test into a failed run, so a solver test that cannot find CBC, or any
other test that decides not to run, fails CI instead of passing quietly. CBC
availability is also reported and enforced separately by
`scripts/report_solver.py` before the suite starts, so a missing solver is
named in its own step.

## Caching cannot hide a dependency

`actions/setup-python` caches pip's download cache, keyed on the lock file.
That cache only saves downloads; it never decides what gets installed. Every
job still installs from the lock into the runner's clean interpreter, and the
smoke test installs the wheel without any extra into a brand-new virtual
environment with no system site packages and no `PYTHONPATH`. A module that
`saw` imports but does not declare is therefore missing there however warm
the cache is, and the smoke test imports every `saw` module to find it.
