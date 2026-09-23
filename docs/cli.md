# Command-line interface

Installing the package (`python -m pip install .`) provides a `saw` command;
`python -m saw` is equivalent. This document defines its stable outcome,
stream, and exit-code contract. The usage section below describes the current
arguments; argument names, input-source selection, and human-oriented output
such as `--help` and `--version` are outside the stable contract.

## Usage

```shell
saw solve problem.json          # read a version 1 problem from a file
saw solve - < problem.json      # read it from standard input
saw solve < problem.json        # same as '-'
```

| Option | Meaning |
| --- | --- |
| `PATH` | Problem file, or `-` (the default) for standard input. |
| `--backend ID` | Backend to solve with (default `exact_enumerated`). |
| `--time-limit SECONDS\|none` | End-to-end wall-clock budget. |
| `--pattern-limit COUNT\|none` | Maximum number of enumerated cut patterns. |
| `--problem-size-limit COUNT\|none` | Maximum combined stock and demand record count. |
| `--numeric-magnitude-limit VALUE\|none` | Maximum stock/demand length or quantity. |
| `--traceback` | On an internal or backend failure (exit codes `6` and `70`), also write the full Python traceback to standard error. Off by default. |
| `saw --version`, `saw --help`, `saw solve --help` | Human-oriented; they print text, not JSON. |

Defaults for the limit options are the `SolveOptions` defaults in
[resource-limits.md](resource-limits.md). When the input is standard input
and it is an interactive terminal, `saw` writes a one-line hint to standard
error before waiting for the problem.

For example, from the repository root:

```shell
saw solve examples/json/problem-v1-valid.json
```

writes the plan in [solution-v1-optimal.json](../examples/json/solution-v1-optimal.json)
(as one line, with run-specific diagnostics) and exits `0`, and

```shell
saw solve --pattern-limit 1 examples/json/problem-v1-valid.json
```

writes a `limit` solution envelope without a plan and exits `4`.

## Standard streams

For every solve invocation, standard output is a machine-readable channel. On
every path where standard output remains writable, it contains exactly one
complete JSON document followed by a single line feed (`\n`, also on
Windows):

- a version 1 solution envelope for a completed solve outcome; or
- a version 1 error envelope for a failure which prevented a normal outcome.

Progress, warnings, tracebacks, solver logs, and other human diagnostics must
never be written to standard output. They may be written to standard error.
Redirecting, suppressing, or closing standard error therefore cannot change
the JSON document on standard output or the exit code. Diagnostics are
written as UTF-8, independent of the console code page. While solving, the CLI redirects Python-level
standard output to standard error, so a stray `print()` or solver log from a
backend or third-party library cannot corrupt the JSON document.

An output write failure is the unavoidable exception: the CLI exits with code
`74`, attempts a concise diagnostic on standard error, and makes no claim that
standard output contains a complete JSON document. The CLI must not print a
second JSON document after a partial write.

## Input decoding

The selected input (a file or standard input) is read completely as bytes and
decoded as strict UTF-8, independent of the console code page or locale. A
single leading UTF-8 byte order mark is ignored. Input that is not UTF-8
(including UTF-16/UTF-32 text with a byte order mark) or that is empty or
whitespace-only was read successfully but is not one JSON document, so it maps
to `invalid_json` with exit code `2`. Only a failure to open or read the
source maps to `input_read_error` with exit code `74`.

Host paths, exception classes, and tracebacks never appear in the error
envelope; the CLI mirrors each error message as a one-line diagnostic on
standard error, where such details may appear. A full traceback is written to
standard error only when requested with `--traceback`.

## Solve path

The CLI is a thin adapter over the public Python API. It parses input with
`problem_from_json`, solves with `solve`, and serializes with
`json_from_solution`. It never calls an optimization backend directly. Option
validation, backend selection, solving, and independent verification all
happen inside `solve()`, so every plan the CLI emits has passed the same
verification gate as a Python caller's result. For the same request and
options, the CLI and Python produce the same semantic result; only
`diagnostics.elapsed_time` may differ between runs.

Each limit option sets exactly one `SolveOptions` field and passes its value
through unchanged. Omitting an option uses the `SolveOptions` default, and the
literal `none` disables the limit:

| Option | `SolveOptions` field |
| --- | --- |
| `--backend` | `backend` |
| `--time-limit` | `time_limit_seconds` |
| `--pattern-limit` | `pattern_limit` |
| `--problem-size-limit` | `problem_size_limit` |
| `--numeric-magnitude-limit` | `numeric_magnitude_limit` |

A value that is not a number maps to `invalid_cli_usage`. A number that
`SolveOptions` validation rejects (such as zero, a negative value, or a
non-finite value) and an unknown backend also map to `invalid_cli_usage`, with
the same message that `solve()` raises. Any other exception raised while
solving is an internal failure (exit code `70`).

## Exit codes

Exit codes are integers in the portable range `0` through `125`. Their meanings
are part of the v0.2 command-line contract:

| Exit code | Outcome | Standard-output document |
| ---: | --- | --- |
| `0` | A verified `optimal` or `feasible` plan was returned. | Solution envelope with the corresponding `solution_status`. |
| `2` | Invocation or input is invalid. | Error envelope with `invalid_cli_usage`, `invalid_json`, `unsupported_format_version`, or `invalid_problem`. |
| `3` | Infeasibility was proved. | Solution envelope with `solution_status: "infeasible"`. |
| `4` | A declared resource limit stopped solving, with or without a verified incumbent. | Solution envelope with `solution_status: "limit"`. |
| `5` | The valid problem is outside the selected backend's supported scope. | Solution envelope with `solution_status: "unsupported"`. |
| `6` | The selected backend is unavailable in the current environment. | Error envelope with `backend_unavailable`. |
| `70` | A backend/internal failure prevented a mathematical result, or an independently checked candidate failed verification. | A solution envelope with `solution_status: "error"`, or an error envelope with `internal_error` or `internal_verification_error`. |
| `74` | Input could not be read or output could not be written. | Error envelope with `input_read_error` when output is writable; `output_write_error` may be unavailable when output itself failed. |

No other exit code is currently documented. A caller must treat an
undocumented code, termination by signal, or a missing/malformed standard-output
document as a process failure outside the Saw outcome contract.

The JSON discriminant remains authoritative within an exit-code class. In
particular, exit code `0` does not distinguish `optimal` from `feasible`, and
exit code `70` does not distinguish a structured backend error result from a
failure before a result existed. Callers inspect `solution_status` or
`error.code` for that detail. A non-zero exit code does not by itself mean that
standard output is an error envelope: infeasible, limit, unsupported, and
structured backend-error outcomes are solution envelopes.

## Failure modes

Every expected failure mode maps to exactly one outcome:

| Failure | Exit code | Standard-output document |
| --- | ---: | --- |
| Unknown option, non-numeric or rejected limit value, unknown backend | `2` | `invalid_cli_usage` |
| Empty, non-UTF-8, malformed, or too deeply nested JSON; duplicate members; `NaN`/`Infinity` | `2` | `invalid_json` |
| Problem `format_version` other than `1` | `2` | `unsupported_format_version` |
| Structurally or semantically invalid problem | `2` | `invalid_problem` |
| Infeasibility proved | `3` | Solution envelope, `infeasible` |
| Size, time, or pattern limit reached | `4` | Solution envelope, `limit` |
| Problem outside the backend's supported scope | `5` | Solution envelope, `unsupported` |
| CBC (or another backend dependency) not available, before or during solving | `6` | `backend_unavailable` |
| Backend reports a failure status | `70` | Solution envelope, `error` |
| Missing or corrupted candidate for `optimal`, `feasible`, or a `limit` incumbent | `70` | `internal_verification_error` |
| Any other exception, or an internally inconsistent result | `70` | `internal_error` |
| Input source cannot be opened or read | `74` | `input_read_error` |
| Standard output cannot be written | `74` | None guaranteed |

An internal failure never produces a plan, an objective value, or an
`optimal`, `feasible`, or `infeasible` status: once solving fails, no
mathematical claim is made. The `internal_error` message is generic; the
exception class and message are written to standard error as a one-line
diagnostic. `--traceback` adds the full traceback to standard error for
internal and backend failures (exit codes `6` and `70`). Tracebacks never
appear on standard output, and input and usage errors (exit code `2`) never
print one.

## Outcome precedence

The CLI emits only the final outcome. Validation occurs before backend
selection or solving, so invalid input maps to exit code `2` even if the
requested backend would also be unavailable. Once a valid problem is accepted,
backend unavailability maps to `6`. A resource limit maps to `4` only when the
solver reports the normal `limit` outcome; an exception or corrupted candidate
is an internal failure and maps to `70`.

This precedence prevents environment details from changing how the same
invalid request is classified and prevents internal failures from being
misreported as expected solve outcomes.

## Interruption

An interrupt (Ctrl+C, `SIGINT`) while saw is working is the caller's own
request, not a Saw outcome. Like termination by any other signal it is outside
the outcome contract: saw writes `saw: interrupted` to standard error, writes
no JSON document, and exits with `130`, the shell convention for `SIGINT`. No
traceback is printed.
