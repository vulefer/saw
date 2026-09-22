# Saw data formats

This document defines Saw's public data-format contracts. The JSON problem and
solution formats are versioned independently from the Saw package and from the
Python models. In this document, **must**, **must not**, and **may** are
normative requirements.

## JSON problem format version 1

Version 1 is the canonical JSON representation of a Saw problem. It serializes
only problem input. The solution contract is specified separately below. This
section does not define a command-line interface or parser behavior beyond the
validation and compatibility rules below.

The companion structural schema is
[`schemas/problem-v1.schema.json`](../schemas/problem-v1.schema.json). This
document remains authoritative for rules that JSON Schema cannot express,
including duplicate object member names, integer spelling, and ID uniqueness.

### Top-level object

A version 1 problem is one JSON object with exactly these members. All lengths
in every version 1 payload are positive integer millimetres (`mm`); the unit is
fixed by the contract and is therefore not repeated in the payload.

| Member | Required value |
| --- | --- |
| `format_version` | The JSON integer `1`. |
| `stocks` | An array of zero or more stock objects. |
| `demands` | An array of zero or more demand objects. |

All three members are required. Unknown members are invalid. A reader must
reject an unsupported `format_version`; it must not guess a version or silently
interpret another version as version 1. Empty `stocks` and `demands` arrays are
valid and retain the meanings defined in the problem specification.

`format_version` identifies the contract used to interpret the payload. It is
independent of the installed Saw package version: multiple package releases may
read or write format version 1. If a future format makes an incompatible change,
it will use a different value so readers can select the correct rules or reject
the payload safely instead of silently misinterpreting it.

### Stock objects

Every member of `stocks` is an object with exactly these required members:

| Member | Type and meaning |
| --- | --- |
| `stock_id` | A non-empty JSON string, unique among stock objects. |
| `material_id` | A non-empty JSON string. |
| `length` | A positive JSON integer measured in millimetres (`mm`). |
| `quantity` | A positive JSON integer giving the available stock count. |

Unknown or missing members are invalid.

### Demand objects

Every member of `demands` is an object with exactly these required members:

| Member | Type and meaning |
| --- | --- |
| `demand_id` | A non-empty JSON string, unique among demand objects. |
| `material_id` | A non-empty JSON string. |
| `length` | A positive JSON integer measured in millimetres (`mm`). |
| `quantity` | A positive JSON integer giving the exact required piece count. |

Unknown or missing members are invalid.

### JSON and primitive-value rules

- The input must be a JSON text as defined by RFC 8259. Duplicate member names
  in any JSON object are invalid, even if the repeated values are equal.
- `length` and `quantity` use JSON integer spelling: an optional minus sign
  followed by base-10 digits, with no decimal point or exponent. The value must
  be greater than zero. Therefore `1.0`, `1e0`, strings such as `"1"`, and the
  Boolean `true` are invalid; readers must not coerce them to integers.
- `format_version` uses the same integer spelling and must equal `1`.
- Numeric values have no smaller format-level upper bound. Backend or resource
  limits are separate from format validity and must not be reported as malformed
  JSON input.
- Every ID is preserved as its decoded JSON string value. Identity is exact and
  case-sensitive; readers must not trim or perform Unicode normalization.
  Consistent with the Python problem contract, a whitespace-only ID is non-empty
  and therefore valid.
- Stock IDs and demand IDs occupy separate namespaces, so a stock and a demand
  may have the same ID. Material IDs are equality values rather than references
  to positional or separately declared material records.

### Identity and ordering

`stock_id`, `demand_id`, and `material_id` are carried explicitly in every
record. Array position and object-member order have no semantic meaning. A
reader may preserve input order for deterministic diagnostics, but it must not
use an array index as record identity or change an ID. Reordering `stocks`,
`demands`, or object members represents the same mathematical problem.

JSON whitespace and escape spelling likewise have no semantic meaning after
decoding. Version 1 defines a canonical data model, not a byte-level JSON
canonicalization scheme.

### Unknown fields and format evolution

Version 1 is a closed contract: unknown fields are invalid at the top level and
inside stock and demand objects. Producers must not add extension fields to a
version 1 payload. A change that adds, removes, renames, or changes the meaning
or type of a field requires a new `format_version`.

### Valid request example

The following request is valid. It is also available as
[`examples/json/problem-v1-valid.json`](../examples/json/problem-v1-valid.json).

```json
{
  "format_version": 1,
  "stocks": [
    {
      "stock_id": "stock-10",
      "material_id": "steel",
      "length": 10,
      "quantity": 2
    }
  ],
  "demands": [
    {
      "demand_id": "demand-6",
      "material_id": "steel",
      "length": 6,
      "quantity": 1
    },
    {
      "demand_id": "demand-4",
      "material_id": "steel",
      "length": 4,
      "quantity": 1
    }
  ]
}
```

### Invalid request examples

This request is invalid because `notes` is an unknown top-level field:

```json
{
  "format_version": 1,
  "stocks": [],
  "demands": [],
  "notes": "extension fields are not allowed"
}
```

This request is invalid because the two stock objects have the same
`stock_id`, even though their other values differ:

```json
{
  "format_version": 1,
  "stocks": [
    {"stock_id": "stock-a", "material_id": "steel", "length": 10, "quantity": 1},
    {"stock_id": "stock-a", "material_id": "steel", "length": 20, "quantity": 1}
  ],
  "demands": []
}
```

This request is invalid because `6.0` uses decimal rather than integer JSON
spelling; no numeric coercion is permitted:

```json
{
  "format_version": 1,
  "stocks": [],
  "demands": [
    {"demand_id": "demand-6", "material_id": "steel", "length": 6.0, "quantity": 1}
  ]
}
```

Potential infeasibility is not a format error. For example, a non-empty demand
array with an empty stock array is a valid version 1 request whose feasibility
must be decided by a solver.

## Python problem contract

The current Python `Problem`, `Stock`, and `Demand` contract and the
mathematical meaning shared by both representations are defined in
[`problem-spec.md`](problem-spec.md). Version 1 JSON field names map directly
to those public model fields; `format_version` is JSON envelope metadata rather
than a field on `Problem`.

## JSON solution format version 1

Version 1 is the canonical JSON representation of a public Saw solution. Its
companion structural schema is
[`schemas/solution-v1.schema.json`](../schemas/solution-v1.schema.json). The
schema and the status rules in this section are one contract: a consumer must
not read a field independently of `solution_status` and infer a stronger claim.

The contract represents completed solver outcomes. Invalid problem input and
failures which the Python API exposes as exceptions are not solution payloads.
In particular, a backend candidate which fails independent verification is not
published as an `error` solution with the invalid candidate attached.

### Top-level object and field presence

Every version 1 solution is one closed JSON object. The following members are
always required:

| Member | Type and meaning |
| --- | --- |
| `format_version` | The JSON integer `1`. |
| `solution_status` | One of the lowercase status strings defined below. |
| `diagnostics` | The diagnostic object defined below. |

The other three top-level members form an indivisible verified-plan group:

| Member | Type and meaning |
| --- | --- |
| `cut_patterns` | The complete array of positive-use cut patterns. |
| `objective_value` | The independently recomputed three-part objective. |
| `verification_report` | The independent verification result for that plan. |

Their presence is determined by status:

| `solution_status` | Verified-plan group | Exact claim |
| --- | --- | --- |
| `optimal` | Required; report must be `valid`. | The plan is verified and global lexicographic optimality was proved. |
| `feasible` | Required; report must be `valid`. | The plan is verified; global lexicographic optimality was not proved. |
| `infeasible` | Forbidden. | A complete method proved that no feasible plan exists. |
| `limit` | Either all three members are present with a `valid` report, or all three are omitted. | A declared resource limit stopped the operation. A present plan is verified, but the status proves neither optimality nor infeasibility. |
| `unsupported` | Forbidden. | The valid problem is outside the selected backend's supported scope. This makes no feasibility claim. |
| `error` | Forbidden. | An internal or backend failure prevents a mathematical claim. |

“Forbidden” means that the member must be omitted, not emitted as `null`, an
empty object, or a placeholder value. Conversely, `optimal` and `feasible`
always include all three plan members; an empty `cut_patterns` array is a real,
verified zero-stock plan for a problem with no demand and is not an absent plan.
This makes absence distinct from an intentionally empty value.

Unknown top-level and nested members are invalid. The version is independent of
the package version and of the problem-format version. A future incompatible
solution change will use another `format_version`.

### Cut patterns

Each member of `cut_patterns` has exactly these fields:

| Member | Type and meaning |
| --- | --- |
| `stock_id` | A non-empty string containing the referenced problem stock ID. |
| `piece_counts` | A non-empty object whose member names are referenced problem demand IDs and whose values are positive integer counts in one use of the pattern. |
| `repetitions` | A positive integer giving the number of times the whole pattern is used. |

Stock and demand IDs are values, never array positions. ID equality is exact and
case-sensitive, using the same rules as problem format version 1. A consumer
must not multiply counts into `piece_counts`: for example, a pattern containing
two pieces of `demand-a` and used three times is represented by
`"piece_counts": {"demand-a": 2}` and `"repetitions": 3`.

Zero-count entries, empty patterns, duplicate canonical patterns, non-positive
counts, and non-positive repetitions are invalid. Two patterns are duplicates
when both `stock_id` and all positive `piece_counts` members are equal,
regardless of JSON object-member order. JSON Schema cannot express that
cross-item identity rule, so readers must check it separately. Pattern-array
order and `piece_counts` member order have no semantic meaning.

### Objective value

`objective_value` contains exactly three non-negative JSON integers:

| Member | Meaning |
| --- | --- |
| `stock_piece_count` | Total stock pieces consumed by all repetitions. |
| `total_leftover` | Total unused length in millimetres across all repetitions. |
| `distinct_pattern_count` | Number of canonical positive-use patterns. |

The tuple is ordered lexicographically in that order by the problem
specification. Its presence proves only that the attached plan was independently
recomputed and matched these values. It does not prove optimality; only
`solution_status: "optimal"` makes that claim.

### Diagnostics

`diagnostics` always contains exactly these members. Every member has one
documented meaning that holds across every `solution_status`; the per-status
table further down states only which *values* are reachable for a given
status, never a second meaning for the same member.

| Member | Type and meaning |
| --- | --- |
| `termination_reason` | A lowercase `TerminationReason` value naming why execution stopped: `completed` for a conclusive mathematical result (optimal, infeasible, or a fully proved stage prefix), a specific limit name (`time_limit`, `pattern_limit`, `size_limit`, ...) when a declared resource budget stopped an otherwise-unfinished attempt, `unsupported_problem` when the backend never started, or a failure reason (`backend_unavailable`, `backend_failure`, `unknown_backend_status`, `internal_error`) when execution could not produce a trustworthy result. |
| `backend_status` | The lowercase normalized `BackendStatus` value the backend itself reported, before translation into the public `solution_status`. It is a strictly internal, backend-facing status: two different `termination_reason` values can share one `backend_status` (for example `limit_reached` paired with either `time_limit` or `pattern_limit`), so callers needing to know *why* must read `termination_reason`, not `backend_status`. |
| `elapsed_time` | A finite, non-negative JSON number of seconds measured once, from entry to `solve()` -- before problem validation -- to the point this diagnostics object was produced (see docs/resource-limits.md, "Evaluation order"). It covers validation, every preflight guard, pattern enumeration, model construction, every objective-stage solver call actually attempted, and, when a candidate is returned, independent verification. It is never a per-stage or per-call figure. |
| `patterns_generated` | A non-negative integer count of complete, non-empty patterns actually produced by enumeration before enumeration stopped, was discarded, or fed model construction. It is `0` whenever enumeration never ran at all (an empty-demand problem, `unsupported`, or a preflight `size_limit`/`time_limit` outcome reached before the backend). A `pattern_limit` outcome reports exactly the configured limit that was reached; every other outcome reports however many patterns enumeration actually produced, which is never rounded up to a configured ceiling it did not reach. |
| `iterations` | A non-negative integer count of backend solver invocations (one `adapter.solve()` call per lexicographic objective stage) *attempted* during this call -- distinct from `completed_objective_stages`, which counts only stages *proved*. Each of the three stages contributes at most `1`, counted the instant its own solver call is made, regardless of whether that call completes with a proven optimum, returns an unproved incumbent, or fails; a stage whose turn never came because the shared deadline was already spent before its call contributes `0`. It is `0` whenever no solver call was ever made (an empty-demand problem, a problem with no feasible non-empty pattern, `unsupported`, or any limit reached before the first solver call). A fully completed `optimal` result reports `3`. |
| `completed_objective_stages` | The ordered prefix of `stock_piece_count`, `total_leftover`, `distinct_pattern_count` *proved* to a global optimum -- for the model as constrained by every earlier stage -- before execution stopped. A stage whose solver call was attempted (and so already counted in `iterations`) but did not return a proven optimum is never included here, even when a plan is still returned as `feasible`. |
| `lower_bound` | A non-negative integer proved lower bound on `stock_piece_count`, or JSON `null` when no bound is reported. Numeric only when some completed or in-progress work actually proves it; never an estimate or a value carried over from an unrelated stage. |
| `upper_bound` | A non-negative integer upper bound on `stock_piece_count` taken from an actual feasible solution (proved or not), or JSON `null` when no such solution is available to report. Never a guess and never smaller than what a genuinely feasible assignment achieves. |

The only valid stage order is `stock_piece_count`, `total_leftover`, then
`distinct_pattern_count`. An `optimal` result contains all three. Other statuses
contain only the completed prefix permitted by the schema; completed earlier
stages do not upgrade the public status.

Numeric bounds concern only `stock_piece_count`. A numeric lower bound must be a
proved lower bound and a numeric upper bound must come from a feasible solution;
producers must use `null` rather than a guess. When both are numeric, the lower
bound must not exceed the upper bound -- and, because both draw from the same
fixed `stock_piece_count` value whenever a stage beyond the primary one is
involved, they are frequently equal rather than merely non-crossing. A bound,
equal bounds, a backend status, or a termination reason never overrides
`solution_status` and never proves the later objective stages.

The schema restricts status/diagnostic combinations. In particular,
`infeasible` requires a normalized `infeasible` backend outcome, `limit`
requires `limit_reached`, and `unsupported` requires `not_run` with
`unsupported_problem`. Error diagnostics use only failure outcomes and reasons.
Because `error` makes no mathematical claim, it also reports no completed
objective stages and uses `null` for both bounds.

#### Diagnostics by `solution_status`

This table exists only to make the schema's status/diagnostic restrictions
above concrete per outcome; it introduces no member and no meaning that is
not already defined by the field table. `#stages` is the length of
`completed_objective_stages`. "Attempted" for `iterations` and
"as-enumerated" for `patterns_generated` always mean the values defined in
the field table above -- the actual count of solver calls made, and of
patterns enumeration actually produced -- never a value fixed in advance by
`solution_status` alone. Two producers may therefore report different
`iterations` or `patterns_generated` for the same `solution_status` on
different inputs; only the *combinations* below are restricted by the
schema. Backend-specific figures (for example, that `exact_enumerated`
always reports `iterations: 3` for `optimal`) are documented in
`docs/resource-limits.md`, not repeated here.

| `solution_status` | `termination_reason` | `patterns_generated` | `iterations` | `#stages` | bounds |
| --- | --- | --- | --- | --- | --- |
| `optimal` | `completed` | as-enumerated | attempted | `3` | `lower_bound == upper_bound`, both the proved stock count |
| `feasible` | a limit reason, or `completed` for an unproved prior-stage incumbent | as-enumerated | attempted, at least `1` (a solver call must have run to produce the incumbent) | `0`-`2` | numeric, both equal to the stock count proved by the last completed stage, when `#stages > 0`; otherwise both `null` |
| `infeasible` | `completed` | as-enumerated | attempted | `0` | both `null` |
| `limit` (with incumbent) | the limit reason that stopped work | as-enumerated | attempted, at least `1` | `0`-`2` | numeric, both equal to the stock count proved by the last completed stage, when `#stages > 0`; otherwise both `null` |
| `limit` (no incumbent) | the limit reason that stopped work | as-enumerated, or the configured ceiling when `pattern_limit` was reached | attempted | `0` | both `null` |
| `unsupported` | `unsupported_problem` | `0` (no backend ever enumerates for a problem it declines) | `0` | `0` | both `null` |
| `error` | a failure reason | as-enumerated, or `0` when the failure happened before enumeration | attempted, including the failing call when the failure happened inside one | `0` | both `null` |

### Python-only diagnostics fields

The Python `Diagnostics` dataclass (`saw.Diagnostics`) carries two members
beyond the JSON table above:

| Member | Type and meaning |
| --- | --- |
| `backend_id` | The identifier of the backend implementation that was reached for this call (for example `"exact_enumerated"`), or `None` when no backend was ever consulted. |
| `backend_version` | The version of that backend's solver dependency (for example the installed PuLP version), or `None` when no backend was consulted, or when the backend could not determine one. |

Both are populated as soon as `solve()` has called the selected backend's
`check_availability()` -- which happens before `unsupported` is decided, so
`unsupported` diagnostics carry them too -- and stay `None` for every outcome
decided before that point: `unsupported`'s preflight-limit siblings
(`size_limit`, and a `time_limit` reached before the backend is ever reached)
never consult a backend at all.

These two fields exist only on the Python object. The version 1 JSON solution
format is a closed contract (see "Unknown fields and format evolution" above
and `schemas/solution-v1.schema.json`'s `additionalProperties: false`):
`json_from_solution()` never writes them, and a future JSON member for backend
or dependency identification would require a new `format_version`, not a
silent addition to version 1. A caller that needs this information from the
JSON CLI output has no wire-format source for it in v1; it is available only
to a direct Python caller of `solve()`.

### Verification report and findings

A verification report has exactly `result` and `findings`. The stable result
strings are:

- `valid`, which requires an empty findings array; and
- `invalid`, which requires one or more structured findings.

Every finding requires a `code` from the public `VerificationFindingCode` enum
and a non-empty `message`. It may include `stock_id`, `demand_id`, or the
zero-based `pattern_index` only when that context applies; absent context fields
are omitted rather than set to `null`.

The schema defines both verification-result representations so the public
verification vocabulary is stable. A plan included in a normal solution
payload must have a `valid` report. An `invalid` report describes a standalone
verification outcome; it is never evidence for a published plan and cannot be
attached to any version 1 solution status.

### JSON primitive and semantic rules

The duplicate-member, integer-spelling, ID-preservation, and unknown-field
rules from problem format version 1 apply equally here. In addition:

- `elapsed_time` must be a finite JSON number; `NaN` and infinities are not JSON
  values;
- counts, repetitions, indices, and bounds use JSON integer spelling without
  coercion;
- a reader validating a solution together with its problem must check that all
  referenced IDs exist and independently enforce the pattern, inventory,
  production, objective, bound, and verification relationships; and
- schema validity establishes representation validity only. It is not an
  independent mathematical verification and must never be presented as one.

### Valid optimal-result example

The following result is valid. It is also available as
[`examples/json/solution-v1-optimal.json`](../examples/json/solution-v1-optimal.json).

```json
{
  "format_version": 1,
  "solution_status": "optimal",
  "cut_patterns": [
    {
      "stock_id": "stock-10",
      "piece_counts": {"demand-4": 1, "demand-6": 1},
      "repetitions": 1
    }
  ],
  "objective_value": {
    "stock_piece_count": 1,
    "total_leftover": 0,
    "distinct_pattern_count": 1
  },
  "diagnostics": {
    "termination_reason": "completed",
    "backend_status": "optimal",
    "elapsed_time": 0.01,
    "patterns_generated": 4,
    "iterations": 3,
    "completed_objective_stages": [
      "stock_piece_count",
      "total_leftover",
      "distinct_pattern_count"
    ],
    "lower_bound": 1,
    "upper_bound": 1
  },
  "verification_report": {
    "result": "valid",
    "findings": []
  }
}
```

For a `limit` result with no verified incumbent, all three plan members are
omitted. Adding only `cut_patterns`, using `null` for `objective_value`, or
attaching an `invalid` report would make the payload invalid.

## JSON error format version 1

Version 1 is the canonical JSON representation of a failure which prevents the
command-line interface from returning a normal solution outcome. Its companion
structural schema is
[`schemas/error-v1.schema.json`](../schemas/error-v1.schema.json). The error
format is independent of both the problem and solution format versions.

Expected mathematical outcomes are not error envelopes. Proven infeasibility,
a declared resource limit, and a valid problem outside a backend's supported
scope use solution envelopes with `solution_status` values `infeasible`,
`limit`, and `unsupported`, respectively. A backend which is not installed or
cannot start is different: it uses an error envelope with
`backend_unavailable`.

### Envelope and required fields

Every version 1 error is one closed JSON object with exactly these members:

| Member | Type and meaning |
| --- | --- |
| `format_version` | The JSON integer `1`. |
| `error` | The closed error object described below. |

The `error` object has exactly two required members. Neither may be omitted or
set to `null`:

| Member | Type and meaning |
| --- | --- |
| `code` | One of the stable lowercase machine-readable codes below. |
| `message` | A non-empty human-readable string. Its wording is not stable and callers must not parse it. |

No stack trace, exception class, backend log, host path, or other diagnostic
field is part of the envelope. Implementations may send such human-oriented
information to standard error, subject to normal security and privacy rules,
but must keep the standard-output JSON closed and deterministic.

### Error codes

| `error.code` | Meaning |
| --- | --- |
| `invalid_cli_usage` | The invocation cannot be interpreted, such as an unknown option or invalid option value. |
| `input_read_error` | The selected input source could not be read completely. |
| `invalid_json` | Input is not one syntactically valid JSON document, including duplicate member names or non-standard numeric constants. |
| `unsupported_format_version` | JSON is readable, but its problem `format_version` is not supported. |
| `invalid_problem` | A version 1 problem violates its structural or semantic contract. |
| `backend_unavailable` | The selected backend cannot be used in the current environment. |
| `internal_verification_error` | A backend candidate failed Saw's independent verification or a required candidate was missing. |
| `internal_error` | An unexpected internal or backend failure prevented a trustworthy normal outcome. |
| `output_write_error` | The selected output could not be written completely; because output itself failed, this envelope is not guaranteed to be observable. |

The codes are category identifiers, not prose and not Python exception names.
Their spelling and meaning are stable for error format version 1. Adding,
removing, or redefining a code requires a new error-format version. More
specific validation findings may appear in a future version, but version 1
does not expose unstable parser paths or exception text as machine data.

The mapping from normal outcomes and error codes to process exit status, and
the separation between machine-readable standard output and human diagnostics,
are defined in [`cli.md`](cli.md).

### Example

This invalid-problem error is also available as
[`examples/json/error-v1-invalid-problem.json`](../examples/json/error-v1-invalid-problem.json):

```json
{
  "format_version": 1,
  "error": {
    "code": "invalid_problem",
    "message": "Demand record 0 has an invalid quantity."
  }
}
```
