# Saw v0.2.0 guarantees

This document states what callers may infer from a v0.2.0 result. The
[problem specification](problem-spec.md) remains the authoritative definition
of the mathematical problem. The practical and feature boundaries are listed
in [limitations.md](limitations.md).

## Supported problem semantics

Saw v0.2.0 supports a bounded, one-dimensional cutting-stock problem through
the Python library and the `saw` command, which share one validation,
solving, and verification path:

- stock and demand lengths are positive integer millimetres;
- stock inventory is a hard per-stock-ID bound;
- multiple stock lengths and multiple independent materials are supported;
- a demand may use only stock with exactly the same material ID;
- demand production is exact, so neither shortage nor surplus is allowed;
- kerf and end trim are zero; and
- feasible plans are minimized lexicographically by consumed stock pieces,
  total leftover, then the number of distinct positive-use patterns.

The default `exact_enumerated` backend constructs every feasible non-empty
pattern within the input bounds and solves all three objective stages. It can
therefore prove optimality or infeasibility when every required stage finishes
conclusively. Completeness does not imply that an instance will finish within
practical time or memory; see [Enumeration and resource use](limitations.md#enumeration-and-resource-use).
How the backend works is explained in [algorithm.md](algorithm.md).

Every call runs under the default resource limits unless the caller changes
them. A limit changes only whether Saw attempts or continues a solve, never the
feasible set or the objective; see the
[resource-limit contract](resource-limits.md).

## Reading a result

`Solution.solution_status` is the claim Saw makes. Callers must inspect it
before using `cut_patterns` or `objective_value`.

| Status | What the caller may infer |
| --- | --- |
| `OPTIMAL` | `cut_patterns` is an independently verified feasible plan, `objective_value` was independently recomputed, and the complete backend proved that no lexicographically better feasible plan exists. |
| `FEASIBLE` | `cut_patterns` is an independently verified feasible plan and `objective_value` was independently recomputed. Global lexicographic optimality was **not** proved. This can occur when a solver limit leaves a usable incumbent. |
| `INFEASIBLE` | The input was valid and a complete method proved that no feasible plan exists. No plan or objective value is returned. |
| `LIMIT` | A declared limit stopped the operation without an optimality or infeasibility proof. This status makes no feasibility claim unless a candidate plan is present with a valid `verification_report`. |
| `UNSUPPORTED` | The input is valid under the public problem contract, but the selected backend does not support the requested option or problem. This says nothing about feasibility. |
| `ERROR` | A backend or internal operation failed, or produced an unknown outcome. This says nothing about feasibility, infeasibility, or optimality. |

A status describes mathematical certainty, while
`diagnostics.termination_reason` describes why execution stopped. For example,
a verified incumbent found before a time limit can be `FEASIBLE` with a
`time_limit` termination reason. Do not infer `OPTIMAL` from a plan's presence,
a completed earlier objective stage, equal-looking bounds, or a successful
verification report.

Invalid input is not an optimization result: Saw raises `ValidationError`
before invoking a backend. An unavailable CBC backend raises
`BackendUnavailableError`. If a backend candidate fails independent
verification, Saw raises `InternalVerificationError` and does not expose that
candidate as a normal solution.

## Independent verification

Before Saw returns a candidate plan with `OPTIMAL` or `FEASIBLE`, an
independent verifier checks the candidate against the original problem. A
candidate attached to a `LIMIT` result must pass the same check. A valid
`verification_report` means that Saw checked:

- every referenced stock and demand ID;
- positive, canonical pattern repetitions;
- material compatibility and per-pattern length capacity;
- bounded inventory for each stock ID;
- exact production for every demand ID; and
- the reported stock count, leftover, and distinct-pattern objective values.

These checks establish feasibility and the accuracy of the reported objective
tuple. They do not enumerate alternative plans and therefore do not, by
themselves, prove optimality. `OPTIMAL` additionally requires conclusive proof
from all three stages of the complete exact backend. `INFEASIBLE` likewise
requires a conclusive infeasibility proof rather than merely failing to find a
plan.

## Diagnostics and bounds

Diagnostics are supporting execution information, not stronger claims than
the public status:

- `completed_objective_stages` lists stages proved optimal before execution
  stopped;
- `patterns_generated` reports the size of the enumerated model;
- `termination_reason` is `time_limit`, `size_limit`, or `pattern_limit` when
  a resource limit stopped the solve;
- `lower_bound` and `upper_bound`, when present, concern the primary
  stock-piece-count objective; and
- a `None` bound means that Saw reports no bound.

Only `OPTIMAL` proves the complete three-part lexicographic objective. Callers
must not treat a primary-objective bound or proof as a proof about leftover or
distinct-pattern count.
