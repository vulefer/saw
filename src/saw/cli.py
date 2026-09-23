"""Command-line entry point for Saw.

This module implements the ``saw`` console script: a thin adapter between
process argv/stdin/stdout/stderr and the public :mod:`saw` API
(:func:`saw.serialization.problem_from_bytes`, :func:`saw.solve.solve`,
:func:`saw.serialization.json_from_solution`,
:func:`saw.serialization.json_from_error`). It owns argument names, input
selection, and human-oriented ``--help``/``--version`` output -- all
explicitly left open by ``docs/cli.md`` -- and implements that document's
standard-stream, exit-code, and outcome-precedence contract exactly.

It intentionally does not know how any backend or resource limit works: a
CLI-supplied limit value is converted from text to a Python number (or
``None``) and handed to :class:`saw.solve.SolveOptions` unchanged, letting
``saw.solve`` alone decide whether the value is valid. This keeps the
argument parser independent of solver implementation details, per
``docs/resource-limits.md``.

The CLI never imports or calls an optimization backend. Parsing,
validation, option checking, backend selection, solving, and independent
verification all happen inside the same public functions a Python caller
uses, so every plan the CLI emits has passed the same verification gate as
:func:`saw.solve.solve`.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import textwrap
import traceback
from importlib import metadata
from dataclasses import dataclass
from typing import IO, Callable

from saw.exceptions import (
    BackendUnavailableError,
    ErrorCode,
    InternalVerificationError,
    SerializationError,
)
from saw.models import SolutionStatus
from saw.serialization import json_from_error, json_from_solution, problem_from_bytes
from saw.solve import InvalidSolveOptionsError, SolveOptions, solve

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

# docs/cli.md, "Exit codes". Every error envelope's exit code is derived from
# its code here, so the two can never disagree.
_ERROR_CODE_EXIT_CODES: dict[ErrorCode, int] = {
    ErrorCode.INVALID_CLI_USAGE: 2,
    ErrorCode.INVALID_JSON: 2,
    ErrorCode.UNSUPPORTED_FORMAT_VERSION: 2,
    ErrorCode.INVALID_PROBLEM: 2,
    ErrorCode.BACKEND_UNAVAILABLE: 6,
    ErrorCode.INTERNAL_ERROR: 70,
    ErrorCode.INTERNAL_VERIFICATION_ERROR: 70,
    ErrorCode.INPUT_READ_ERROR: 74,
    ErrorCode.OUTPUT_WRITE_ERROR: 74,
}

# docs/cli.md, "Interruption": 128 + SIGINT, the shell convention. Outside
# the outcome contract, so no document is written for it.
_INTERRUPTED_EXIT_CODE = 130

# Longest argument text echoed back in a usage error message.
_MAX_ECHOED_ARGUMENT_LENGTH = 40


class _CliUsageError(Exception):
    """An argparse-detected usage problem, routed to the JSON error contract."""


class _InputReadError(Exception):
    """The selected input source could not be read completely (exit 74).

    ``str(error)`` is the envelope message and names only the kind of
    source; ``detail`` may name a host path and the OS error, so it is only
    ever written to standard error (docs/formats.md, "Envelope and required
    fields").
    """

    def __init__(self, message: str, detail: str) -> None:
        super().__init__(message)
        self.detail = detail


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


def _optional_value_type(
    convert: Callable[[str], object], expected: str,
) -> Callable[[str], object]:
    """Build an argparse ``type=`` callable that also accepts ``none``.

    Only performs the text-to-number conversion and the "none" -> ``None``
    sentinel handling; it deliberately does not check sign or finiteness,
    since those rules belong to ``saw.solve`` alone.
    """

    def convert_or_none(raw: str) -> object:
        if raw.strip().lower() == "none":
            return None
        try:
            return convert(raw)
        except ValueError as error:
            shown = raw
            if len(shown) > _MAX_ECHOED_ARGUMENT_LENGTH:
                shown = shown[: _MAX_ECHOED_ARGUMENT_LENGTH - 3] + "..."
            if raw.strip().lstrip("+-").isdigit():
                # Only reachable past Python's integer string-length limit.
                reason = "the number has too many digits"
            else:
                reason = f"expected {expected} or 'none'"
            raise argparse.ArgumentTypeError(
                f"invalid value {shown!r}: {reason}"
            ) from error

    return convert_or_none


@dataclass(frozen=True)
class _LimitFlag:
    """One ``--*-limit`` option and the ``SolveOptions`` field it sets."""

    flag: str
    field: str
    convert: Callable[[str], object]
    expected: str
    metavar: str
    help: str


# The explicit, complete CLI -> SolveOptions mapping for resource limits.
# Each flag sets exactly the named SolveOptions field and nothing else;
# _solve_options_from_args builds SolveOptions from this table alone, and a
# unit test keeps it in one-to-one correspondence with SolveOptions' fields.
_LIMIT_FLAGS: tuple[_LimitFlag, ...] = (
    _LimitFlag(
        flag="--time-limit",
        field="time_limit_seconds",
        convert=float,
        expected="a number of seconds",
        metavar="SECONDS|none",
        help="End-to-end wall-clock budget for the solve, in seconds",
    ),
    _LimitFlag(
        flag="--pattern-limit",
        field="pattern_limit",
        convert=int,
        expected="an integer",
        metavar="COUNT|none",
        help="Maximum number of cut patterns enumerated",
    ),
    _LimitFlag(
        flag="--problem-size-limit",
        field="problem_size_limit",
        convert=int,
        expected="an integer",
        metavar="COUNT|none",
        help="Maximum combined stock and demand record count",
    ),
    _LimitFlag(
        flag="--numeric-magnitude-limit",
        field="numeric_magnitude_limit",
        convert=int,
        expected="an integer",
        metavar="VALUE|none",
        help="Maximum stock/demand length or quantity",
    ),
)


def _solve_options_from_args(args: argparse.Namespace) -> SolveOptions:
    """Map parsed arguments onto ``SolveOptions`` field by field.

    Values pass through unchanged; ``solve()`` validates them exactly as it
    would for a Python caller and raises ``InvalidSolveOptionsError`` for an
    invalid one.
    """
    return SolveOptions(
        backend=args.backend,
        **{limit.field: getattr(args, limit.field) for limit in _LIMIT_FLAGS},
    )


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
            "Input encoding:\n"
            + textwrap.indent(
                textwrap.fill(
                    "Input is read as bytes and must be UTF-8 (RFC 8259), "
                    "regardless of the console or locale encoding. A leading "
                    "UTF-8 byte order mark is ignored. Empty input is "
                    "reported as invalid_json."
                ),
                "  ",
            )
            + "\n\n"
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
    for limit in _LIMIT_FLAGS:
        default = getattr(_DEFAULT_OPTIONS, limit.field)
        parser.add_argument(
            limit.flag,
            dest=limit.field,
            type=_optional_value_type(limit.convert, limit.expected),
            default=default,
            metavar=limit.metavar,
            help=f"{limit.help}, or 'none' to disable it (default: {default}).",
        )
    parser.add_argument(
        "--traceback",
        action="store_true",
        help=(
            "On an internal or backend failure, also write the full Python "
            "traceback to standard error. Standard output is unaffected."
        ),
    )
    parser.set_defaults(handler=_run_solve)


def _is_process_stream(stream: object) -> bool:
    """Whether ``stream`` is the process's own stdout or stderr.

    Only these may be reconfigured or have their file descriptor redirected;
    streams injected by a caller (tests) are never touched.
    """
    return stream is not None and any(
        stream is candidate
        for candidate in (sys.stdout, sys.__stdout__, sys.stderr, sys.__stderr__)
    )


def _discard_further_writes(stream: IO[str] | None) -> None:
    """Point a failed process stream's file descriptor at the null device.

    After a failed write, text may still sit in the stream's buffer; the
    interpreter retries flushing it at shutdown, fails again on the broken
    pipe, prints "Exception ignored ... BrokenPipeError", and forces exit
    code 120 instead of the documented one. Redirecting the descriptor makes
    that final flush succeed silently. Applies to standard output and
    standard error alike, since a closed stderr must never change the exit
    code either (docs/cli.md, "Standard streams").
    """
    if not _is_process_stream(stream):
        return
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(devnull, stream.fileno())
        finally:
            os.close(devnull)
    except (OSError, ValueError, AttributeError):
        pass


def _print_diagnostic(stderr: IO[str] | None, text: str) -> None:
    """Best-effort human diagnostic on standard error.

    Standard error is advisory (docs/cli.md): a closed or broken stderr must
    never change the exit code or the standard-output document.
    """
    if stderr is None:
        return
    try:
        print(text, file=stderr)
        stderr.flush()
    except (OSError, ValueError):
        _discard_further_writes(stderr)


def _print_traceback(stderr: IO[str] | None) -> None:
    if stderr is None:
        return
    try:
        traceback.print_exc(file=stderr)
        stderr.flush()
    except (OSError, ValueError):
        _discard_further_writes(stderr)


class _StderrSink(io.TextIOBase):
    """A text stream that forwards writes to standard error, best effort.

    Installed as ``sys.stdout`` while ``solve()`` runs, so a stray
    ``print()`` or solver log from a backend or a third-party library lands
    on standard error instead of corrupting the one JSON document on
    standard output (docs/cli.md, "Standard streams"). Write failures are
    swallowed: standard error is advisory and must never turn into a solve
    failure.
    """

    def __init__(self, stderr: IO[str] | None) -> None:
        super().__init__()
        self._stderr = stderr

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        if self._stderr is not None:
            try:
                self._stderr.write(text)
            except (OSError, ValueError, AttributeError):
                _discard_further_writes(self._stderr)
        return len(text)

    def flush(self) -> None:
        if self._stderr is not None:
            try:
                self._stderr.flush()
            except (OSError, ValueError, AttributeError):
                _discard_further_writes(self._stderr)


def _configure_process_streams(stdout: IO[str], stderr: IO[str]) -> None:
    """Make the process streams' bytes independent of platform and locale.

    Standard output gets ``\\n`` line endings on every platform, so the JSON
    document is byte-for-byte identical on Windows. Standard error is written
    as UTF-8, matching the input decoding rule, so a diagnostic naming a
    non-ASCII path is not mangled by a legacy code page.
    """
    if _is_process_stream(stdout):
        with contextlib.suppress(AttributeError, OSError, ValueError):
            stdout.reconfigure(newline="\n")
    if _is_process_stream(stderr):
        with contextlib.suppress(AttributeError, OSError, ValueError):
            stderr.reconfigure(encoding="utf-8", errors="backslashreplace")


def _write_document(stdout: IO[str], stderr: IO[str], text: str) -> int | None:
    """Write one JSON document plus a trailing newline to ``stdout``.

    Returns ``None`` on success, or the exit code ``74`` (and a stderr
    diagnostic) on an output write failure, including a broken pipe when
    the reader exits early. Never writes a second document on failure, per
    docs/cli.md.
    """
    try:
        stdout.write(text)
        stdout.write("\n")
        stdout.flush()
    except (OSError, ValueError, AttributeError) as error:
        # OSError covers BrokenPipeError (EPIPE) and Windows' EINVAL for a
        # closed pipe; ValueError is a closed stream; AttributeError is a
        # missing stream (sys.stdout is None under pythonw).
        _discard_further_writes(stdout)
        _print_diagnostic(stderr, f"{PROG}: could not write output: {error}")
        return _ERROR_CODE_EXIT_CODES[ErrorCode.OUTPUT_WRITE_ERROR]
    return None


def _emit_error(
    stdout: IO[str],
    stderr: IO[str],
    code: ErrorCode,
    message: str,
    *,
    diagnostic: str | None = None,
) -> int:
    """Write an error envelope to stdout and a human diagnostic to stderr.

    Returns the exit code documented for ``code``. ``diagnostic`` replaces
    ``message`` on stderr when it carries details (host paths, exception
    classes) that must stay out of the closed envelope (docs/formats.md,
    "Envelope and required fields").
    """
    _print_diagnostic(stderr, f"{PROG}: error: {diagnostic or message}")
    write_failure = _write_document(stdout, stderr, json_from_error(code, message))
    return write_failure if write_failure is not None else _ERROR_CODE_EXIT_CODES[code]


def _is_terminal(stream: IO[str]) -> bool:
    try:
        return stream.isatty()
    except (AttributeError, OSError, ValueError):
        return False


def _read_input(path: str, stdin: IO[str] | None) -> bytes:
    """Read the whole problem from ``path`` or, for ``-``, stdin, as bytes.

    Decoding is left to :func:`saw.serialization.problem_from_bytes`, so it
    never depends on the console code page or locale encoding. A text-only
    stream without a ``.buffer`` (such as an injected ``io.StringIO``) is
    re-encoded as UTF-8.

    Raises:
        _InputReadError: the source could not be opened or read completely.
    """
    if path == "-":
        if stdin is None:
            raise _InputReadError(
                "Could not read standard input.",
                "could not read standard input: it is not available",
            )
        try:
            buffer = getattr(stdin, "buffer", None)
            if buffer is not None:
                return buffer.read()
            # surrogatepass keeps undecodable text undecodable, so it is
            # still reported as invalid_json rather than crashing here.
            return stdin.read().encode("utf-8", "surrogatepass")
        except (OSError, ValueError) as error:
            raise _InputReadError(
                "Could not read standard input.",
                f"could not read standard input: {error}",
            ) from error

    try:
        with open(path, "rb") as handle:
            return handle.read()
    except (OSError, ValueError) as error:
        raise _InputReadError(
            "Could not read the input file.",
            # strerror avoids repeating the path OSError already embeds.
            f"could not read input file '{path}': "
            f"{getattr(error, 'strerror', None) or error}",
        ) from error


def _run_solve(
    args: argparse.Namespace,
    *,
    stdin: IO[str],
    stdout: IO[str],
    stderr: IO[str],
) -> int:
    if args.input == "-" and stdin is not None and _is_terminal(stdin):
        # Otherwise an interactive "saw solve" just waits, silently.
        _print_diagnostic(
            stderr,
            f"{PROG}: reading the problem from standard input; end it with "
            "Ctrl+D (Ctrl+Z then Enter on Windows), or pass a file path.",
        )

    try:
        problem_bytes = _read_input(args.input, stdin)
    except _InputReadError as error:
        return _emit_error(
            stdout, stderr, ErrorCode.INPUT_READ_ERROR, str(error),
            diagnostic=error.detail,
        )

    try:
        problem = problem_from_bytes(problem_bytes)
    except SerializationError as error:
        # Read completely but not a valid version 1 problem: invalid
        # content, not an I/O failure, so rerunning cannot help (exit 2).
        return _emit_error(stdout, stderr, error.error_code, str(error))

    options = _solve_options_from_args(args)

    try:
        # The only solving entry point: solve() validates the options,
        # selects the backend, runs it, and independently verifies any
        # candidate before returning it (docs/guarantees.md).
        with contextlib.redirect_stdout(_StderrSink(stderr)):
            solution = solve(problem, options)
    except InvalidSolveOptionsError as error:
        # A caller configuration error (docs/resource-limits.md), not a
        # solve outcome. Any other ValueError is an internal failure and is
        # left to main()'s internal_error mapping (docs/cli.md, "Outcome
        # precedence").
        return _emit_error(stdout, stderr, ErrorCode.INVALID_CLI_USAGE, str(error))
    except BackendUnavailableError as error:
        if args.traceback:
            _print_traceback(stderr)
        return _emit_error(stdout, stderr, ErrorCode.BACKEND_UNAVAILABLE, str(error))
    except InternalVerificationError as error:
        if args.traceback:
            _print_traceback(stderr)
        return _emit_error(
            stdout, stderr, ErrorCode.INTERNAL_VERIFICATION_ERROR, str(error),
        )

    try:
        payload = json_from_solution(solution)
    except SerializationError as error:
        # solve() returned an internally inconsistent Solution: a saw bug,
        # not a CLI usage or input problem.
        if args.traceback:
            _print_traceback(stderr)
        return _emit_error(stdout, stderr, ErrorCode.INTERNAL_ERROR, str(error))

    # Resolved before writing: once the solution envelope is on standard
    # output, a failure here could only be reported with a second document.
    exit_code = _SOLUTION_STATUS_EXIT_CODES.get(solution.solution_status)
    if exit_code is None:
        return _emit_error(
            stdout, stderr, ErrorCode.INTERNAL_ERROR,
            "An internal error prevented a result; see standard error.",
            diagnostic=(
                "internal error: solve() returned an unmapped solution "
                f"status {solution.solution_status!r}"
            ),
        )

    write_failure = _write_document(stdout, stderr, payload)
    return write_failure if write_failure is not None else exit_code


def main(
    argv: list[str] | None = None,
    *,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
    stderr: IO[str] | None = None,
) -> int:
    """Run the CLI and return its documented exit code (docs/cli.md).

    Streams default to the process's ``sys`` streams and may be injected for
    testing. Every path writes at most one JSON document to ``stdout``.
    """
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    _configure_process_streams(stdout, stderr)

    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except _CliUsageError as error:
        return _emit_error(stdout, stderr, ErrorCode.INVALID_CLI_USAGE, str(error))

    handler = getattr(args, "handler", None)
    if handler is None:  # pragma: no cover - unreachable while "command" is required
        return _emit_error(
            stdout, stderr, ErrorCode.INVALID_CLI_USAGE,
            "No command given. Run 'saw --help' for usage.",
        )

    try:
        return handler(args, stdin=stdin, stdout=stdout, stderr=stderr)
    except KeyboardInterrupt:
        # Ctrl+C is the caller's own request, not a saw failure: a one-line
        # note instead of a traceback, and no document (docs/cli.md,
        # "Interruption").
        _print_diagnostic(stderr, f"{PROG}: interrupted")
        return _INTERRUPTED_EXIT_CODE
    except Exception as error:  # noqa: BLE001 - last-resort internal_error mapping
        # The exception class and traceback are human diagnostics that
        # docs/formats.md keeps out of the envelope: they go only to stderr,
        # and the full traceback only on request.
        if getattr(args, "traceback", False):
            _print_traceback(stderr)
            hint = ""
        else:
            hint = " (rerun with --traceback for the full traceback)"
        return _emit_error(
            stdout, stderr, ErrorCode.INTERNAL_ERROR,
            "An internal error prevented a result; see standard error.",
            diagnostic=f"internal error: {type(error).__name__}: {error}{hint}",
        )


if __name__ == "__main__":
    sys.exit(main())
