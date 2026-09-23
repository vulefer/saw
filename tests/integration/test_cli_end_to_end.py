"""End-to-end tests for the installed ``saw`` command (Linear V020-16).

Every test here runs the ``saw`` console script that ``pip install`` placed
next to the running interpreter as a separate operating-system process. It
feeds a problem through a real file or real standard input and inspects only
what an external caller can see: the raw standard-output bytes, the raw
standard-error bytes, and the exit code. Nothing is monkeypatched, so these
tests cover the entry-point wiring, stream encoding, and process exit status
that the in-process tests in ``tests/unit/test_cli*.py`` cannot reach.

For every run, standard output must be exactly one newline-terminated JSON
document that validates against the published version 1 schema, and the exit
code must be the one ``docs/cli.md`` assigns to that document. Returned plans
are checked by ``_independent_findings``, a verifier written against the JSON
format alone that shares no code with ``saw.verification``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sysconfig
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


REPOSITORY_ROOT = Path(__file__).parents[2]
SCHEMA_DIRECTORY = REPOSITORY_ROOT / "schemas"
DOCUMENTED_PROBLEM = REPOSITORY_ROOT / "examples" / "json" / "problem-v1-valid.json"
DOCUMENTED_SOLUTION = REPOSITORY_ROOT / "examples" / "json" / "solution-v1-optimal.json"

# docs/cli.md, "Exit codes".
SOLUTION_STATUS_EXIT_CODES = {
    "optimal": 0,
    "feasible": 0,
    "infeasible": 3,
    "limit": 4,
    "unsupported": 5,
    "error": 70,
}
ERROR_CODE_EXIT_CODES = {
    "invalid_cli_usage": 2,
    "invalid_json": 2,
    "unsupported_format_version": 2,
    "invalid_problem": 2,
    "backend_unavailable": 6,
    "internal_error": 70,
    "internal_verification_error": 70,
    "input_read_error": 74,
    "output_write_error": 74,
}

# Two materials, two steel stock lengths, and demands that cannot all fit on
# one stock piece, so the returned plan exercises every rule the independent
# verifier checks.
MULTI_MATERIAL_PROBLEM = {
    "format_version": 1,
    "stocks": [
        {"stock_id": "steel-12", "material_id": "steel", "length": 12, "quantity": 2},
        {"stock_id": "steel-7", "material_id": "steel", "length": 7, "quantity": 3},
        {"stock_id": "oak-9", "material_id": "oak", "length": 9, "quantity": 2},
    ],
    "demands": [
        {"demand_id": "steel-5", "material_id": "steel", "length": 5, "quantity": 3},
        {"demand_id": "steel-4", "material_id": "steel", "length": 4, "quantity": 2},
        {"demand_id": "steel-3", "material_id": "steel", "length": 3, "quantity": 1},
        {"demand_id": "oak-4", "material_id": "oak", "length": 4, "quantity": 3},
    ],
}

# Each demand fits on the stock length, but there is only one stock piece for
# two 6 mm demands, so infeasibility has to be proved by the backend rather
# than rejected by validation.
INFEASIBLE_PROBLEM = {
    "format_version": 1,
    "stocks": [
        {"stock_id": "stock-10", "material_id": "steel", "length": 10, "quantity": 1},
    ],
    "demands": [
        {"demand_id": "demand-6", "material_id": "steel", "length": 6, "quantity": 2},
    ],
}


@dataclass(frozen=True)
class CliRun:
    exit_code: int
    document: dict
    stderr: str


@pytest.fixture(scope="module")
def saw_executable() -> str:
    """The ``saw`` console script installed for the running interpreter.

    Only the interpreter's own scripts directories are searched, never the
    whole ``PATH``, so the tests cannot pick up a ``saw`` from another
    environment. A missing command fails instead of skipping: exercising the
    installed command is the purpose of this module.
    """
    directories = [sysconfig.get_path("scripts")]
    user_scheme = f"{os.name}_user"
    if user_scheme in sysconfig.get_scheme_names():
        directories.append(sysconfig.get_path("scripts", user_scheme))
    for directory in directories:
        found = shutil.which("saw", path=directory)
        if found is not None:
            return found
    pytest.fail(
        "The 'saw' console script is not installed for this interpreter "
        f"(searched {directories}); install the package with "
        "'python -m pip install -e .' before running the integration tests."
    )


@pytest.fixture(scope="module")
def validators() -> dict[str, Draft202012Validator]:
    result = {}
    for kind in ("solution", "error"):
        schema = json.loads(
            (SCHEMA_DIRECTORY / f"{kind}-v1.schema.json").read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(schema)
        result[kind] = Draft202012Validator(schema)
    return result


@pytest.fixture
def run_saw(saw_executable, validators, tmp_path):
    """Run ``saw`` as an external process and check the stream contract.

    Every run, whatever its outcome, must satisfy docs/cli.md: standard
    output is exactly one schema-valid, newline-terminated JSON document; the
    exit code is the one documented for that document; and no traceback
    appears on either stream (none of these runs pass ``--traceback``).
    """

    def run(argv: list[str], *, stdin: bytes = b"") -> CliRun:
        completed = subprocess.run(
            [saw_executable, *argv],
            input=stdin,
            capture_output=True,
            # A neutral working directory: the command must not depend on
            # being run from the repository checkout.
            cwd=tmp_path,
            timeout=120,
            check=False,
        )
        stdout = completed.stdout.decode("utf-8")
        stderr = completed.stderr.decode("utf-8", errors="replace")

        document = _parse_exactly_one_document(stdout)
        kind = "error" if "error" in document else "solution"
        validators[kind].validate(document)

        if kind == "error":
            expected_exit_code = ERROR_CODE_EXIT_CODES[document["error"]["code"]]
            # Every error envelope is mirrored as a one-line human
            # diagnostic on standard error.
            assert stderr.startswith("saw: error: "), stderr
        else:
            expected_exit_code = SOLUTION_STATUS_EXIT_CODES[document["solution_status"]]
        assert completed.returncode == expected_exit_code, (document, stderr)

        assert "Traceback" not in stdout
        assert "Traceback" not in stderr, stderr
        return CliRun(completed.returncode, document, stderr)

    return run


def _parse_exactly_one_document(stdout: str) -> dict:
    """Parse ``stdout`` as one JSON object and a single trailing newline.

    ``json.loads`` alone would also accept surrounding whitespace, so the
    document is decoded with ``raw_decode`` and whatever follows it must be
    exactly the terminating newline -- no second document, no log line.
    Standard output is a text stream, so on Windows that newline is
    ``\\r\\n``.
    """
    document, end = json.JSONDecoder().raw_decode(stdout)
    assert stdout[end:] in ("\n", "\r\n"), repr(stdout[end:])
    assert isinstance(document, dict)
    return document


def _without_elapsed_time(document: dict) -> dict:
    stripped = json.loads(json.dumps(document))
    stripped["diagnostics"].pop("elapsed_time")
    return stripped


def _independent_findings(problem: dict, solution: dict) -> list[str]:
    """Re-check a returned plan against the problem using only the JSON.

    Deliberately shares no code with ``saw.verification``: it implements the
    feasibility and objective rules of docs/problem-spec.md directly (exact
    production, zero kerf, bounded inventory, matching materials, no
    duplicate patterns) so that a bug in Saw's own verifier cannot also hide
    a bad plan here. Returns a list of human-readable violations.
    """
    stocks = {stock["stock_id"]: stock for stock in problem["stocks"]}
    demands = {demand["demand_id"]: demand for demand in problem["demands"]}
    findings: list[str] = []
    stock_used: Counter[str] = Counter()
    produced: Counter[str] = Counter()
    seen_patterns: set[tuple[str, tuple[tuple[str, int], ...]]] = set()
    total_leftover = 0

    for index, pattern in enumerate(solution["cut_patterns"]):
        label = f"cut_patterns[{index}]"
        stock = stocks.get(pattern["stock_id"])
        repetitions = pattern["repetitions"]
        if stock is None:
            findings.append(f"{label}: unknown stock {pattern['stock_id']!r}")
            continue
        if not isinstance(repetitions, int) or repetitions < 1:
            findings.append(f"{label}: repetitions {repetitions!r} is not positive")
            continue

        key = (stock["stock_id"], tuple(sorted(pattern["piece_counts"].items())))
        if key in seen_patterns:
            findings.append(f"{label}: duplicates an earlier pattern")
        seen_patterns.add(key)

        used_length = 0
        for demand_id, count in pattern["piece_counts"].items():
            demand = demands.get(demand_id)
            if demand is None:
                findings.append(f"{label}: unknown demand {demand_id!r}")
                continue
            if not isinstance(count, int) or count < 1:
                findings.append(f"{label}: count {count!r} for {demand_id!r} is not positive")
                continue
            if demand["material_id"] != stock["material_id"]:
                findings.append(f"{label}: {demand_id!r} cut from a different material")
            used_length += demand["length"] * count
            produced[demand_id] += count * repetitions

        if not pattern["piece_counts"]:
            findings.append(f"{label}: cuts no pieces")
        if used_length > stock["length"]:
            findings.append(
                f"{label}: uses {used_length} mm of a {stock['length']} mm stock"
            )
        stock_used[stock["stock_id"]] += repetitions
        total_leftover += (stock["length"] - used_length) * repetitions

    for stock_id, used in stock_used.items():
        if used > stocks[stock_id]["quantity"]:
            findings.append(
                f"stock {stock_id!r}: {used} pieces used, "
                f"{stocks[stock_id]['quantity']} available"
            )
    for demand_id, demand in demands.items():
        if produced[demand_id] != demand["quantity"]:
            findings.append(
                f"demand {demand_id!r}: produced {produced[demand_id]}, "
                f"required exactly {demand['quantity']}"
            )

    expected_objective = {
        "stock_piece_count": sum(stock_used.values()),
        "total_leftover": total_leftover,
        "distinct_pattern_count": len(seen_patterns),
    }
    if solution["objective_value"] != expected_objective:
        findings.append(
            f"objective_value {solution['objective_value']} does not match "
            f"the plan {expected_objective}"
        )
    return findings


def _assert_independently_verified(problem: dict, solution: dict) -> None:
    assert solution["verification_report"] == {"result": "valid", "findings": []}
    assert _independent_findings(problem, solution) == []


# --- input sources and successful output -----------------------------------


def test_file_input_produces_the_documented_verified_solution(run_saw) -> None:
    run = run_saw(["solve", str(DOCUMENTED_PROBLEM)])

    assert run.exit_code == 0
    assert run.stderr == ""
    documented = json.loads(DOCUMENTED_SOLUTION.read_text(encoding="utf-8"))
    for key in ("format_version", "solution_status", "cut_patterns",
                "objective_value", "verification_report"):
        assert run.document[key] == documented[key], key
    _assert_independently_verified(
        json.loads(DOCUMENTED_PROBLEM.read_text(encoding="utf-8")), run.document,
    )


@pytest.mark.parametrize(
    "argv", [["solve", "-"], ["solve"]], ids=["dash", "no-path"],
)
def test_standard_input_gives_the_same_result_as_file_input(run_saw, argv) -> None:
    from_file = run_saw(["solve", str(DOCUMENTED_PROBLEM)])
    from_stdin = run_saw(argv, stdin=DOCUMENTED_PROBLEM.read_bytes())

    assert from_stdin.exit_code == 0
    assert _without_elapsed_time(from_stdin.document) == _without_elapsed_time(
        from_file.document
    )


def test_standard_input_accepts_a_utf8_byte_order_mark(run_saw) -> None:
    run = run_saw(["solve"], stdin=b"\xef\xbb\xbf" + DOCUMENTED_PROBLEM.read_bytes())

    assert run.exit_code == 0
    assert run.document["solution_status"] == "optimal"


def test_multi_material_plan_is_independently_verified(run_saw, tmp_path) -> None:
    problem_path = tmp_path / "problem.json"
    problem_path.write_text(json.dumps(MULTI_MATERIAL_PROBLEM), encoding="utf-8")

    run = run_saw(["solve", str(problem_path)])

    assert run.exit_code == 0
    assert run.document["solution_status"] == "optimal"
    _assert_independently_verified(MULTI_MATERIAL_PROBLEM, run.document)
    # An optimal claim carries a proved bound equal to the plan's primary
    # objective.
    stock_count = run.document["objective_value"]["stock_piece_count"]
    assert run.document["diagnostics"]["lower_bound"] == stock_count
    assert run.document["diagnostics"]["upper_bound"] == stock_count


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda s: s["cut_patterns"][0].update(repetitions=3),
        lambda s: s["cut_patterns"][0]["piece_counts"].update({"demand-6": 2}),
        lambda s: s["cut_patterns"][0].update(stock_id="stock-missing"),
        lambda s: s["objective_value"].update(total_leftover=1),
        lambda s: s["cut_patterns"].append(dict(s["cut_patterns"][0])),
    ],
    ids=["over-inventory", "over-length", "unknown-stock", "objective", "duplicate"],
)
def test_the_independent_verifier_rejects_a_corrupted_plan(corrupt) -> None:
    """Guards the guard: the test-side verifier must not accept anything."""
    problem = json.loads(DOCUMENTED_PROBLEM.read_text(encoding="utf-8"))
    solution = json.loads(DOCUMENTED_SOLUTION.read_text(encoding="utf-8"))
    assert _independent_findings(problem, solution) == []

    corrupt(solution)

    assert _independent_findings(problem, solution) != []


# --- invalid invocation and input ------------------------------------------


def _problem_bytes(*, format_version: int = 1, stock_length: int = 10) -> bytes:
    """The documented problem with one field changed, as UTF-8 bytes."""
    problem = json.loads(DOCUMENTED_PROBLEM.read_text(encoding="utf-8"))
    problem["format_version"] = format_version
    problem["stocks"][0]["length"] = stock_length
    return json.dumps(problem).encode("utf-8")



@pytest.mark.parametrize(
    ("argv", "stdin", "expected_code"),
    [
        (["solve", "--no-such-option"], b"", "invalid_cli_usage"),
        (["solve", "--time-limit", "soon"], b"", "invalid_cli_usage"),
        (["solve", "--pattern-limit", "0"], b"", "invalid_cli_usage"),
        (["solve", "--backend", "no-such-backend"], b"", "invalid_cli_usage"),
        (["solve"], b"", "invalid_json"),
        (["solve"], b"{", "invalid_json"),
        (["solve"], b'{"format_version": 1, "format_version": 1}', "invalid_json"),
        (["solve"], "﻿{}".encode("utf-16"), "invalid_json"),
        (["solve"], _problem_bytes(format_version=2), "unsupported_format_version"),
        (["solve"], _problem_bytes(stock_length=-10), "invalid_problem"),
    ],
    ids=[
        "unknown-option", "non-numeric-limit", "rejected-limit", "unknown-backend",
        "empty", "malformed", "duplicate-member", "utf-16", "format-version-2",
        "negative-length",
    ],
)
def test_invalid_invocation_or_input_exits_2_with_an_error_envelope(
    run_saw, argv, stdin, expected_code,
) -> None:
    # A valid problem accompanies the usage errors, so only the invocation
    # itself can be what is rejected.
    if expected_code == "invalid_cli_usage":
        stdin = DOCUMENTED_PROBLEM.read_bytes()

    run = run_saw(argv, stdin=stdin)

    assert run.exit_code == 2
    assert run.document["error"]["code"] == expected_code
    # Exactly the envelope message, mirrored once; no traceback for exit 2.
    assert run.stderr.splitlines() == [
        f"saw: error: {run.document['error']['message']}"
    ]


def test_unreadable_input_file_exits_74_without_leaking_the_path(
    run_saw, tmp_path,
) -> None:
    missing = tmp_path / "missing-problem.json"

    run = run_saw(["solve", str(missing)])

    assert run.exit_code == 74
    assert run.document["error"]["code"] == "input_read_error"
    # The host path is a human diagnostic: stderr only, never the envelope.
    assert "missing-problem.json" not in json.dumps(run.document)
    assert "missing-problem.json" in run.stderr


# --- expected non-optimal outcomes -----------------------------------------


def test_proved_infeasibility_exits_3_without_a_plan(run_saw) -> None:
    run = run_saw(["solve"], stdin=json.dumps(INFEASIBLE_PROBLEM).encode("utf-8"))

    assert run.exit_code == 3
    assert run.document["solution_status"] == "infeasible"
    assert run.document["diagnostics"]["backend_status"] == "infeasible"
    for key in ("cut_patterns", "objective_value", "verification_report"):
        assert key not in run.document


@pytest.mark.parametrize(
    ("flags", "termination_reason", "patterns_generated"),
    [
        (["--problem-size-limit", "1"], "size_limit", 0),
        (["--numeric-magnitude-limit", "5"], "size_limit", 0),
        (["--pattern-limit", "1"], "pattern_limit", 1),
        # Shorter than process startup alone, so the deadline has always
        # passed by the first check after validation.
        (["--time-limit", "1e-9"], "time_limit", 0),
    ],
    ids=["problem-size", "numeric-magnitude", "pattern", "time"],
)
def test_a_reached_limit_exits_4_without_a_mathematical_claim(
    run_saw, flags, termination_reason, patterns_generated,
) -> None:
    run = run_saw(["solve", *flags, str(DOCUMENTED_PROBLEM)])

    assert run.exit_code == 4
    assert run.document["solution_status"] == "limit"
    diagnostics = run.document["diagnostics"]
    assert diagnostics["termination_reason"] == termination_reason
    assert diagnostics["backend_status"] == "limit_reached"
    assert diagnostics["patterns_generated"] == patterns_generated
    assert diagnostics["iterations"] == 0
    assert diagnostics["completed_objective_stages"] == []
    assert diagnostics["lower_bound"] is None
    assert diagnostics["upper_bound"] is None
    for key in ("cut_patterns", "objective_value", "verification_report"):
        assert key not in run.document


def test_disabling_a_limit_with_none_restores_the_verified_optimum(run_saw) -> None:
    limited = run_saw(["solve", "--pattern-limit", "1", str(DOCUMENTED_PROBLEM)])
    unlimited = run_saw(["solve", "--pattern-limit", "none", str(DOCUMENTED_PROBLEM)])

    assert limited.exit_code == 4
    assert unlimited.exit_code == 0
    _assert_independently_verified(
        json.loads(DOCUMENTED_PROBLEM.read_text(encoding="utf-8")), unlimited.document,
    )
