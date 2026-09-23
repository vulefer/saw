# Command-line outcome contract

This document defines the stable outcome, stream, and exit-code contract for
the Saw JSON command-line interface before its implementation. Argument names,
input-source selection, and optional human-oriented commands such as `--help`
are outside this contract.

## Standard streams

For every solve invocation, standard output is a machine-readable channel. On
every path where standard output remains writable, it contains exactly one
complete JSON document followed by a newline:

- a version 1 solution envelope for a completed solve outcome; or
- a version 1 error envelope for a failure which prevented a normal outcome.

Progress, warnings, tracebacks, solver logs, and other human diagnostics must
never be written to standard output. They may be written to standard error.
Redirecting or suppressing standard error therefore cannot change the JSON
document on standard output.

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
