# the saw command, a thin layer between the terminal and the python api
# reads the input, calls problem_from_bytes -> solve -> json_from_solution,
# and writes exactly one json document to stdout with the right exit code
# limit values are passed to SolveOptions unchanged, solve() decides if they are valid
# never calls a backend directly, so every answer goes through the same verification
# the full contract is in docs/cli.md

from __future__ import annotations

import argparse
from collections.abc import Callable
import contextlib
from dataclasses import dataclass
from importlib import metadata
import io
import os
import sys
import textwrap
import traceback
from typing import IO, NoReturn, TypeGuard, cast

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

# exit code for each solution status, see docs/cli.md "Exit codes"
_SOLUTION_STATUS_EXIT_CODES: dict[SolutionStatus, int] = {
    SolutionStatus.OPTIMAL: 0,
    SolutionStatus.FEASIBLE: 0,
    SolutionStatus.INFEASIBLE: 3,
    SolutionStatus.LIMIT: 4,
    SolutionStatus.UNSUPPORTED: 5,
    SolutionStatus.ERROR: 70,
}

# used to read the default option values, with slots=True SolveOptions.<field>
# doesnt give the default, so an instance is needed
_DEFAULT_OPTIONS = SolveOptions()

# exit code for each error code, see docs/cli.md "Exit codes"
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

# exit code for ctrl+c (128 + SIGINT, the shell convention), no json is written for it
_INTERRUPTED_EXIT_CODE = 130

# longest bad argument shown back in a usage error message
_MAX_ECHOED_ARGUMENT_LENGTH = 40


# raised for bad command line arguments, reported as invalid_cli_usage
class _CliUsageError(Exception):
    pass


# raised when the input file or stdin cannot be read (exit 74)
# the message goes in the json, detail can contain a file path so it only goes to stderr
class _InputReadError(Exception):
    def __init__(self, message: str, detail: str) -> None:
        super().__init__(message)
        self.detail = detail


# argparse normally prints an error and exits, this raises instead
# so a json error can be written to stdout, --help and --version still work normally
class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _CliUsageError(message)


# the installed saw version, for --version
def _package_version() -> str:
    try:
        return metadata.version("saw")
    except metadata.PackageNotFoundError:
        return "unknown"


# builds the converter argparse uses for a limit flag, text -> number, or "none" -> None
# doesnt check if the number is positive or finite, solve() does that
def _optional_value_type(
    convert: Callable[[str], object],
    expected: str,
) -> Callable[[str], object]:

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
                # only happens when the number is longer than python allows
                reason = "the number has too many digits"
            else:
                reason = f"expected {expected} or 'none'"
            raise argparse.ArgumentTypeError(f"invalid value {shown!r}: {reason}") from error

    return convert_or_none


# one --*-limit flag and the SolveOptions field it sets
@dataclass(frozen=True)
class _LimitFlag:
    flag: str
    field: str
    convert: Callable[[str], object]
    expected: str
    metavar: str
    help: str


# every limit flag and the SolveOptions field it sets
# a unit test makes sure this matches the SolveOptions fields one to one
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


# builds SolveOptions from the parsed arguments, values are passed unchanged
# and checked by solve() the same way as for a python caller
def _solve_options_from_args(args: argparse.Namespace) -> SolveOptions:
    return SolveOptions(
        backend=args.backend,
        **{limit.field: getattr(args, limit.field) for limit in _LIMIT_FLAGS},
    )


# builds the argument parser for the saw command
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


# adds "saw solve" with its arguments and help text
def _add_solve_subcommand(
    subparsers: argparse._SubParsersAction[_ArgumentParser],
) -> None:
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


# True if the stream is the real stdout or stderr, only those are ever changed,
# streams passed in by tests are left alone
def _is_process_stream(stream: IO[str] | None) -> TypeGuard[IO[str]]:
    return stream is not None and any(
        stream is candidate
        for candidate in (sys.stdout, sys.__stdout__, sys.stderr, sys.__stderr__)
    )


# after a failed write, points the stream at the null device
# otherwise python retries the flush on exit, fails again and changes the exit code to 120
def _discard_further_writes(stream: IO[str] | None) -> None:
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


# prints a message for people to stderr, if stderr is broken it is ignored
# so it never changes the exit code or the json output
def _print_diagnostic(stderr: IO[str] | None, text: str) -> None:
    if stderr is None:
        return
    try:
        print(text, file=stderr)
        stderr.flush()
    except (OSError, ValueError):
        _discard_further_writes(stderr)


# same as _print_diagnostic but prints the current traceback, used for --traceback
def _print_traceback(stderr: IO[str] | None) -> None:
    if stderr is None:
        return
    try:
        traceback.print_exc(file=stderr)
        stderr.flush()
    except (OSError, ValueError):
        _discard_further_writes(stderr)


# stands in for stdout while solve() runs and sends everything to stderr,
# so a stray print() or solver log cant break the json on stdout
# write failures are ignored, stderr must never make the solve fail
class _StderrSink(io.TextIOBase):
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


# makes the output the same on every platform, stdout always uses \n line endings
# (also on windows) and stderr is utf-8 so non-ascii paths dont get mangled
def _configure_process_streams(stdout: IO[str], stderr: IO[str]) -> None:
    # the real streams are TextIOWrappers, anything else raises AttributeError
    if _is_process_stream(stdout):
        with contextlib.suppress(AttributeError, OSError, ValueError):
            cast(io.TextIOWrapper, stdout).reconfigure(newline="\n")
    if _is_process_stream(stderr):
        with contextlib.suppress(AttributeError, OSError, ValueError):
            cast(io.TextIOWrapper, stderr).reconfigure(encoding="utf-8", errors="backslashreplace")


# writes one json document and a newline to stdout
# returns None on success, or exit code 74 if writing failed (e.g. a broken pipe),
# a second document is never written
def _write_document(stdout: IO[str], stderr: IO[str], text: str) -> int | None:
    try:
        stdout.write(text)
        stdout.write("\n")
        stdout.flush()
    except (OSError, ValueError, AttributeError) as error:
        # OSError is a broken or closed pipe, ValueError is a closed stream,
        # AttributeError is no stdout at all (e.g. under pythonw)
        _discard_further_writes(stdout)
        _print_diagnostic(stderr, f"{PROG}: could not write output: {error}")
        return _ERROR_CODE_EXIT_CODES[ErrorCode.OUTPUT_WRITE_ERROR]
    return None


# writes the json error to stdout and a message to stderr, returns the exit code
# diagnostic replaces the message on stderr when it has details like file paths
# that must stay out of the json
def _emit_error(
    stdout: IO[str],
    stderr: IO[str],
    code: ErrorCode,
    message: str,
    *,
    diagnostic: str | None = None,
) -> int:
    _print_diagnostic(stderr, f"{PROG}: error: {diagnostic or message}")
    write_failure = _write_document(stdout, stderr, json_from_error(code, message))
    return write_failure if write_failure is not None else _ERROR_CODE_EXIT_CODES[code]


# True if the stream is an interactive terminal and not a file or pipe
def _is_terminal(stream: IO[str]) -> bool:
    try:
        return stream.isatty()
    except (AttributeError, OSError, ValueError):
        return False


# reads the whole problem as bytes from the file, or from stdin for "-"
# decoding is left to problem_from_bytes so the console encoding doesnt matter
# raises _InputReadError if it cant be read
def _read_input(path: str, stdin: IO[str] | None) -> bytes:
    if path == "-":
        if stdin is None:
            raise _InputReadError(
                "Could not read standard input.",
                "could not read standard input: it is not available",
            )
        try:
            buffer = getattr(stdin, "buffer", None)
            if buffer is not None:
                data: bytes = buffer.read()
                return data
            # a text only stream (e.g. StringIO in tests) is turned back into bytes,
            # surrogatepass keeps bad text bad so it is reported as invalid_json
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
            # strerror is used so the path isnt shown twice
            f"could not read input file '{path}': {getattr(error, 'strerror', None) or error}",
        ) from error


# runs "saw solve": read -> parse -> solve -> turn into json -> write
# every failure along the way becomes a json error with its exit code
def _run_solve(
    args: argparse.Namespace,
    *,
    stdin: IO[str],
    stdout: IO[str],
    stderr: IO[str],
) -> int:
    if args.input == "-" and stdin is not None and _is_terminal(stdin):
        # without this, "saw solve" typed in a terminal would just wait silently
        _print_diagnostic(
            stderr,
            f"{PROG}: reading the problem from standard input; end it with "
            "Ctrl+D (Ctrl+Z then Enter on Windows), or pass a file path.",
        )

    try:
        problem_bytes = _read_input(args.input, stdin)
    except _InputReadError as error:
        return _emit_error(
            stdout,
            stderr,
            ErrorCode.INPUT_READ_ERROR,
            str(error),
            diagnostic=error.detail,
        )

    try:
        problem = problem_from_bytes(problem_bytes)
    except SerializationError as error:
        # the input was read but isnt a valid problem, exit 2
        return _emit_error(stdout, stderr, error.error_code, str(error))

    options = _solve_options_from_args(args)

    try:
        # solve() does everything including verification, anything printed
        # while it runs is sent to stderr
        with contextlib.redirect_stdout(_StderrSink(stderr)):
            solution = solve(problem, options)
    except InvalidSolveOptionsError as error:
        # a bad option value from the user, any other ValueError is a bug
        # and is handled in main() as internal_error
        return _emit_error(stdout, stderr, ErrorCode.INVALID_CLI_USAGE, str(error))
    except BackendUnavailableError as error:
        if args.traceback:
            _print_traceback(stderr)
        return _emit_error(stdout, stderr, ErrorCode.BACKEND_UNAVAILABLE, str(error))
    except InternalVerificationError as error:
        if args.traceback:
            _print_traceback(stderr)
        return _emit_error(
            stdout,
            stderr,
            ErrorCode.INTERNAL_VERIFICATION_ERROR,
            str(error),
        )

    try:
        payload = json_from_solution(solution)
    except SerializationError as error:
        # solve() returned a solution that contradicts itself, a saw bug
        if args.traceback:
            _print_traceback(stderr)
        return _emit_error(stdout, stderr, ErrorCode.INTERNAL_ERROR, str(error))

    # exit code is found before writing, after writing a failure would need a second document
    exit_code = _SOLUTION_STATUS_EXIT_CODES.get(solution.solution_status)
    if exit_code is None:
        return _emit_error(
            stdout,
            stderr,
            ErrorCode.INTERNAL_ERROR,
            "An internal error prevented a result; see standard error.",
            diagnostic=(
                "internal error: solve() returned an unmapped solution "
                f"status {solution.solution_status!r}"
            ),
        )

    write_failure = _write_document(stdout, stderr, payload)
    return write_failure if write_failure is not None else exit_code


# entry point of the saw command, returns the exit code
# streams default to the real ones and can be passed in by tests
# every path writes at most one json document to stdout
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
    _configure_process_streams(stdout, stderr)

    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except _CliUsageError as error:
        return _emit_error(stdout, stderr, ErrorCode.INVALID_CLI_USAGE, str(error))

    handler = getattr(args, "handler", None)
    if handler is None:  # pragma: no cover - unreachable while "command" is required
        return _emit_error(
            stdout,
            stderr,
            ErrorCode.INVALID_CLI_USAGE,
            "No command given. Run 'saw --help' for usage.",
        )

    try:
        exit_code: int = handler(args, stdin=stdin, stdout=stdout, stderr=stderr)
        return exit_code
    except KeyboardInterrupt:
        # ctrl+c is the user's choice, not a failure, so just a short note and no json
        _print_diagnostic(stderr, f"{PROG}: interrupted")
        return _INTERRUPTED_EXIT_CODE
    except Exception as error:  # noqa: BLE001 - last-resort internal_error mapping
        # any other exception is a bug, reported as internal_error
        # the exception details only go to stderr, the traceback only with --traceback
        if getattr(args, "traceback", False):
            _print_traceback(stderr)
            hint = ""
        else:
            hint = " (rerun with --traceback for the full traceback)"
        return _emit_error(
            stdout,
            stderr,
            ErrorCode.INTERNAL_ERROR,
            "An internal error prevented a result; see standard error.",
            diagnostic=f"internal error: {type(error).__name__}: {error}{hint}",
        )


if __name__ == "__main__":
    sys.exit(main())
