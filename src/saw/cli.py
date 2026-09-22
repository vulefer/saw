"""Command-line entry point for Saw.

This module implements the ``saw`` console script: a thin adapter between
process argv/stdin/stdout/stderr and the public :mod:`saw` API
(:func:`saw.serialization.problem_from_json`, :func:`saw.solve.solve`,
:func:`saw.serialization.json_from_solution`). It owns argument names, input
selection, and human-oriented ``--help``/``--version`` output -- all
explicitly left open by ``docs/cli.md`` -- and implements that document's
standard-stream, exit-code, and outcome-precedence contract exactly.

It intentionally does not know how any backend or resource limit works: a
CLI-supplied limit value is converted from text to a Python number (or
``None``) and handed to :class:`saw.solve.SolveOptions` unchanged, letting
``saw.solve`` alone decide whether the value is valid. This keeps the
argument parser independent of solver implementation details, per
``docs/resource-limits.md``.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
import traceback
from importlib import metadata
from typing import IO, Callable

from saw.exceptions import (
    BackendUnavailableError,
    InternalVerificationError,
    SerializationError,
    SerializationErrorCode,
)
from saw.models import SolutionStatus
from saw.serialization import json_from_solution, problem_from_json
from saw.solve import SolveOptions, solve

__all__ = ("build_parser", "main")

PROG = "saw"

# docs/cli.md, "Exit codes".
_SOLUTION_STATUS_EXIT_CODES: dict[SolutionStatus, int] = {
    SolutionStatus.OPTIMAL: 0,
    SolutionStatus.FEASIBLE: 0,
    SolutionStatus.INFEASIBLE: 3,
    SolutionStatus.LIMIT: 4,
    SolutionStatus.UNSUPPORTED: 5,
    SolutionStatus.ERROR: 70,
}

# SolveOptions uses slots=True, so SolveOptions.<field> is a slot
# descriptor, not the default value -- an instance is needed to read the
# actual v0.2.0 defaults for help text and argparse defaults below.
_DEFAULT_OPTIONS = SolveOptions()

# docs/formats.md, "Error codes": a SerializationError raised while reading a
# problem is always a readable-but-invalid document, never a CLI usage or
# I/O problem, so it always maps to exit code 2.
_SERIALIZATION_ERROR_CODES: dict[SerializationErrorCode, str] = {
    SerializationErrorCode.INVALID_JSON: "invalid_json",
    SerializationErrorCode.UNSUPPORTED_FORMAT_VERSION: "unsupported_format_version",
    SerializationErrorCode.SCHEMA_VIOLATION: "invalid_problem",
    SerializationErrorCode.SEMANTIC_VALIDATION: "invalid_problem",
}


class _CliUsageError(Exception):
    """An argparse-detected usage problem, routed to the JSON error contract."""


class _ArgumentParser(argparse.ArgumentParser):
    """Raises instead of printing to stderr and calling ``sys.exit(2)``.

    ``docs/cli.md`` requires standard output to carry a JSON error envelope
    for invalid invocations (exit code 2, ``invalid_cli_usage``), which
    argparse's default error handling cannot produce. ``-h``/``--help`` and
    ``--version`` are unaffected: those actions call ``parser.exit()``
    directly and are documented as outside this contract.
    """

    def error(self, message: str) -> None:
        raise _CliUsageError(message)


def _package_version() -> str:
    try:
        return metadata.version("saw")
    except metadata.PackageNotFoundError:
        return "unknown"


def _optional_value_type(convert: Callable[[str], object]) -> Callable[[str], object]:
    """Build an argparse ``type=`` callable that also accepts ``none``.

    Only performs the text-to-number conversion "none" -> ``None`` sentinel
    handling; it deliberately does not check sign, finiteness, or
    integer-ness, since those rules belong to ``saw.solve`` alone.
    """

    def convert_or_none(raw: str) -> object:
        if raw.strip().lower() == "none":
            return None
        try:
            return convert(raw)
        except ValueError as error:
            raise argparse.ArgumentTypeError(str(error)) from error

    return convert_or_none


def build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog=PROG,
        description="Saw: a correctness-first 1D cutting-stock solver.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {_package_version()}",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_solve_subcommand(subparsers)
    return parser


def _add_solve_subcommand(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "solve",
        help="Solve a version 1 JSON cutting-stock problem.",
        description=textwrap.fill(
            "Read a version 1 JSON problem, solve it, and write exactly one "
            "version 1 JSON document -- a solution envelope or an error "
            "envelope -- to standard output. Progress, warnings, and other "
            "human diagnostics are written to standard error only. See "
            "docs/cli.md and docs/formats.md for the full contract."
        ),
        epilog=(
            "Input modes:\n"
            "  saw solve problem.json    read the problem from a file\n"
            "  saw solve -               read the problem from standard input\n"
            "  saw solve                 same as '-': read from standard input\n"
            "\n"
            "Output behavior:\n"
            + textwrap.indent(
                textwrap.fill(
                    "Standard output always contains exactly one JSON "
                    "document (newline-terminated) on every path where it "
                    "remains writable: a solution envelope for a completed "
                    "outcome, or an error envelope for a failure that "
                    "prevented one. The process exit code encodes the "
                    "outcome; see docs/cli.md."
                ),
                "  ",
            )
            + "\n\n"
            "Limit options:\n"
            + textwrap.indent(
                textwrap.fill(
                    "Each --*-limit option accepts a value or the literal "
                    "'none' to disable that limit for this run, mirroring "
                    "saw.solve.SolveOptions. Omitting an option uses its "
                    "v0.2.0 safe default; see docs/resource-limits.md."
                ),
                "  ",
            )
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "input",
        nargs="?",
        default="-",
        metavar="PATH",
        help=(
            "Path to a version 1 JSON problem file, or '-' (the default) "
            "to read the problem from standard input."
        ),
    )
    parser.add_argument(
        "--backend",
        default=_DEFAULT_OPTIONS.backend,
        metavar="ID",
        help=f"Backend to solve with (default: {_DEFAULT_OPTIONS.backend!r}).",
    )
    parser.add_argument(
        "--time-limit",
        dest="time_limit_seconds",
        type=_optional_value_type(float),
        default=_DEFAULT_OPTIONS.time_limit_seconds,
        metavar="SECONDS|none",
        help=(
            "End-to-end wall-clock budget for the solve, in seconds, or "
            f"'none' to disable it (default: {_DEFAULT_OPTIONS.time_limit_seconds})."
        ),
    )
    parser.add_argument(
        "--pattern-limit",
        dest="pattern_limit",
        type=_optional_value_type(int),
        default=_DEFAULT_OPTIONS.pattern_limit,
        metavar="COUNT|none",
        help=(
            "Maximum number of cut patterns enumerated, or 'none' to "
            f"disable it (default: {_DEFAULT_OPTIONS.pattern_limit})."
        ),
    )
    parser.add_argument(
        "--problem-size-limit",
        dest="problem_size_limit",
        type=_optional_value_type(int),
        default=_DEFAULT_OPTIONS.problem_size_limit,
        metavar="COUNT|none",
        help=(
            "Maximum combined stock and demand record count, or 'none' to "
            f"disable it (default: {_DEFAULT_OPTIONS.problem_size_limit})."
        ),
    )
    parser.add_argument(
        "--numeric-magnitude-limit",
        dest="numeric_magnitude_limit",
        type=_optional_value_type(int),
        default=_DEFAULT_OPTIONS.numeric_magnitude_limit,
        metavar="VALUE|none",
        help=(
            "Maximum stock/demand length or quantity, or 'none' to "
            f"disable it (default: {_DEFAULT_OPTIONS.numeric_magnitude_limit})."
        ),
    )
    parser.set_defaults(handler=_run_solve)


def _write_document(stdout: IO[str], stderr: IO[str], text: str) -> int | None:
    """Write one JSON document plus a trailing newline to ``stdout``.

    Returns ``None`` on success, or the exit code ``74`` (and a stderr
    diagnostic) on an output write failure. Never writes a second document
    on failure, per docs/cli.md.
    """
    try:
        stdout.write(text)
        stdout.write("\n")
        stdout.flush()
    except OSError as error:
        print(f"{PROG}: could not write output: {error}", file=stderr)
        return 74
    return None


def _emit_error(
    stdout: IO[str],
    stderr: IO[str],
    code: str,
    message: str,
    exit_code: int,
) -> int:
    document = json.dumps(
        {"format_version": 1, "error": {"code": code, "message": message}},
        allow_nan=False,
    )
    write_failure = _write_document(stdout, stderr, document)
    return write_failure if write_failure is not None else exit_code


def _read_input(path: str, stdin: IO[str]) -> str:
    if path == "-":
        return stdin.read()
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _run_solve(
    args: argparse.Namespace,
    *,
    stdin: IO[str],
    stdout: IO[str],
    stderr: IO[str],
) -> int:
    try:
        problem_text = _read_input(args.input, stdin)
    except (OSError, UnicodeDecodeError) as error:
        return _emit_error(
            stdout, stderr, "input_read_error",
            f"Could not read input {args.input!r}: {error}", exit_code=74,
        )

    try:
        problem = problem_from_json(problem_text)
    except SerializationError as error:
        return _emit_error(
            stdout, stderr, _SERIALIZATION_ERROR_CODES[error.code],
            str(error), exit_code=2,
        )

    options = SolveOptions(
        backend=args.backend,
        time_limit_seconds=args.time_limit_seconds,
        pattern_limit=args.pattern_limit,
        problem_size_limit=args.problem_size_limit,
        numeric_magnitude_limit=args.numeric_magnitude_limit,
    )

    try:
        solution = solve(problem, options)
    except ValueError as error:
        # solve() reserves ValueError for invalid SolveOptions, a caller
        # configuration error (docs/resource-limits.md), not a solve outcome.
        return _emit_error(
            stdout, stderr, "invalid_cli_usage", str(error), exit_code=2,
        )
    except BackendUnavailableError as error:
        return _emit_error(
            stdout, stderr, "backend_unavailable", str(error), exit_code=6,
        )
    except InternalVerificationError as error:
        return _emit_error(
            stdout, stderr, "internal_verification_error", str(error),
            exit_code=70,
        )

    try:
        payload = json_from_solution(solution)
    except SerializationError as error:
        # solve() returned an internally inconsistent Solution: a saw bug,
        # not a CLI usage or input problem.
        return _emit_error(
            stdout, stderr, "internal_error", str(error), exit_code=70,
        )

    write_failure = _write_document(stdout, stderr, payload)
    if write_failure is not None:
        return write_failure
    return _SOLUTION_STATUS_EXIT_CODES[solution.solution_status]


def main(
    argv: list[str] | None = None,
    *,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
    stderr: IO[str] | None = None,
) -> int:
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr

    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except _CliUsageError as error:
        return _emit_error(
            stdout, stderr, "invalid_cli_usage", str(error), exit_code=2,
        )

    handler = getattr(args, "handler", None)
    if handler is None:  # pragma: no cover - unreachable while "command" is required
        return _emit_error(
            stdout, stderr, "invalid_cli_usage",
            "No command given. Run 'saw --help' for usage.", exit_code=2,
        )

    try:
        return handler(args, stdin=stdin, stdout=stdout, stderr=stderr)
    except Exception as error:  # noqa: BLE001 - last-resort internal_error mapping
        # The full traceback is a human diagnostic (docs/cli.md, "Standard
        # streams"): it goes only to stderr, never into the stdout JSON.
        traceback.print_exc(file=stderr)
        return _emit_error(
            stdout, stderr, "internal_error",
            f"{type(error).__name__}: {error}", exit_code=70,
        )


if __name__ == "__main__":
    sys.exit(main())
