# Saw v0.2.0 resource-limit contract

This document defines the resource budgets used by Saw v0.2.0. These limits
control whether Saw attempts or continues a solve; they do not add constraints
to the cutting-stock problem and do not change its feasible set or objective.
Saw never clamps an input value, drops a stock or demand record, or optimizes a
truncated pattern set in order to remain under a limit.

## Configuration and safe defaults

`SolveOptions` exposes the four independent limits below. Omitting an option
uses its safe default. Passing `None` disables only that limit, and passing a
valid non-default value raises or lowers it for that call. There is no process
environment variable or implicit backend setting that overrides these values.

| Option | Measured unit and inclusive test | Valid configured values | v0.2.0 default |
| --- | --- | --- | --- |
| `time_limit_seconds` | Seconds of monotonic elapsed wall-clock time for one call to `solve` | A finite real number greater than zero, or `None` | `30.0` seconds |
| `problem_size_limit` | Input records; `len(problem.stocks) + len(problem.demands)` must be at most the limit | A positive integer, or `None` | `200` records |
| `numeric_magnitude_limit` | The value of each `Stock.length`, `Stock.quantity`, `Demand.length`, and `Demand.quantity`, in that field's unit, must be at most the limit | A positive integer, or `None` | `1_000_000` field units |
| `pattern_limit` | Complete, non-empty patterns generated across all stock records; enumeration may produce at most the limit | A positive integer, or `None` | `50_000` patterns |

Booleans are not numbers for this contract. Zero, negative values, non-finite
floating-point values, fractional values for integer limits, and values of any
other type are invalid options and raise `ValueError` before backend selection.
An invalid limit is a caller configuration error, not a solution result.

The defaults are operational guardrails for unfamiliar callers, not promises
that every accepted problem will finish within a particular amount of memory
or before the time deadline. Advanced callers may increase or disable an
individual guard after considering the enumeration risks described in
[limitations.md](limitations.md).

## Evaluation order

The timer starts on entry to `solve`, before problem validation. JSON parsing,
CLI startup, result serialization, and time spent by the caller constructing a
`Problem` are outside the budget. The budget includes:

1. domain and resource validation;
2. pattern enumeration;
3. optimization-model construction;
4. all three lexicographic objective stages; and
5. independent verification of any candidate returned to the caller.

Domain validity has precedence over resource outcomes. Saw completes the
ordinary deterministic validation pass first, so a malformed domain value or
duplicate ID remains a `ValidationError` even if the elapsed-time or magnitude
guard would also be exceeded. After domain validation, limits are checked in
this deterministic order:

1. `problem_size_limit`;
2. `numeric_magnitude_limit`;
3. the remaining end-to-end time; and
4. `pattern_limit` while enumerating.

During enumeration Saw checks the time deadline before checking whether the
next pattern would exceed the pattern limit. Consequently, an already-expired
deadline is reported as `time_limit`; otherwise an attempted `(limit + 1)`th
pattern is reported as `pattern_limit`. Generating exactly `pattern_limit`
patterns is allowed if enumeration is then complete.

## One end-to-end time budget

`time_limit_seconds` is one deadline for the entire solve, not a new allowance
for each objective stage. Before every backend call Saw computes:

```text
remaining_seconds = deadline - current_monotonic_time
```

If the remainder is not positive, Saw does not start that stage. Otherwise the
backend receives only `remaining_seconds`. The secondary and tertiary stages
therefore consume what remains after validation, enumeration, model building,
and earlier optimization; they never receive the original configured value
again.

Deadline checks are cooperative. Saw checks at phase boundaries and inside
its validation, enumeration, model-building, and verification loops. An
individual backend operation may return slightly after its assigned remainder,
so the option is not a hard real-time or operating-system CPU quota. A backend
must not be started again after the shared deadline has expired.

## Termination and incumbent rules

Resource limits are expected solve outcomes. They do not raise
`ValidationError`, do not become `unsupported`, and never imply infeasibility
or optimality. Diagnostics use the following reasons:

| Limit that stopped work | `termination_reason` | `patterns_generated` | v0.2.0 exact-backend incumbent policy |
| --- | --- | --- | --- |
| End-to-end time | `time_limit` | Number fully generated when work stopped | Return the best available integer incumbent only after full independent verification |
| Problem record count | `size_limit` | `0` | No incumbent; optimization was not started |
| Numeric magnitude | `size_limit` | `0` | No incumbent; optimization was not started |
| Pattern count | `pattern_limit` | Exactly the configured limit | No incumbent; the partial enumeration is discarded and never optimized |

Problem size and numeric magnitude deliberately share `size_limit`: both are
pre-solve size guards, while the detailed configured option identifies which
test was applied. The version 1 solution format does not expose untrusted or
partially enumerated data.

If a time limit stops the exact backend without an integer incumbent, Saw
returns `solution_status: "limit"`, `backend_status: "limit_reached"`, and no
plan group. If an integer incumbent from any completed or interrupted
objective stage is available and passes the complete independent verifier,
Saw returns `solution_status: "feasible"`, `backend_status: "feasible"`, and
the required plan, objective, and valid verification report. The termination
reason remains `time_limit`, and `completed_objective_stages` lists only the
proved lexicographic prefix. A candidate that cannot finish verification
before the deadline is omitted and cannot support a `feasible` result.

Earlier-stage incumbents remain eligible after a later stage reaches the time
limit because fixing already-proved objective values preserves feasibility.
Only proved primary-objective bounds may appear in `lower_bound`; only a fully
verified incumbent may supply `upper_bound`. Missing proof is represented by
`null`, never by an estimate.

If all optimization stages and independent verification complete before a
limit stops the operation, the normal `optimal` or `infeasible` outcome wins.
A configured threshold that was reached but not exceeded does not replace a
completed mathematical result with a limit result.

## Compatibility with the v0.1.0 problem

These defaults change which problems Saw attempts automatically, not the
v0.1.0 mathematical problem. A problem admitted under the resource policy is
solved with the same stock bounds, exact-demand constraints, and lexicographic
objective. A problem stopped by a guard receives no fabricated mathematical
claim. Passing `None` for all four limits restores the unguarded v0.1.0
resource behavior, including its risk of excessive time or memory use.

