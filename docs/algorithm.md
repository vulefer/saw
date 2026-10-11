# How Saw solves a problem

This document explains how the default `exact_enumerated` backend turns a
validated problem into a verified result. The mathematical problem itself is
defined in [problem-spec.md](problem-spec.md), the resource budgets in
[resource-limits.md](resource-limits.md), and what a result lets you conclude
in [guarantees.md](guarantees.md). Where this overview and those documents
disagree, they take precedence.

## Overview

A call to `solve(problem, options)` runs these steps, all inside one
end-to-end time budget:

1. Validate the problem and the options.
2. Apply the pre-solve size guards.
3. Enumerate every feasible cutting pattern.
4. Build one integer program over those patterns.
5. Solve three lexicographic objective stages with CBC.
6. Independently verify the candidate plan.
7. Return a structured result with an honest status and diagnostics.

The command-line interface parses JSON into the same `Problem` and calls the
same `solve`, so it shares every step below.

## 1. Validation

Every stock and demand record is checked against the problem specification:
non-empty string IDs, unique IDs, positive integer lengths and quantities, and
so on. Invalid input raises `ValidationError` before any solver work starts;
it is never reported as infeasible. `SolveOptions` are validated the same way
and an invalid limit raises `ValueError`.

## 2. Pre-solve size guards

With the default options Saw refuses, before enumerating anything, a problem
with more than `problem_size_limit` records or any length or quantity above
`numeric_magnitude_limit`. Such a problem gets a `LIMIT` result with
termination reason `size_limit`; it is not a statement about feasibility.
Each guard can be raised or disabled per call.

## 3. Pattern enumeration

A *pattern* is one way to cut a single piece of one stock record: a count of
pieces for each demand of the same material whose total length fits the stock
length. For every stock record Saw walks a depth-first tree over the
compatible demands. At each level it chooses how many pieces of that demand to
cut, from zero up to the smaller of the demand quantity and what still fits in
the remaining length. Every non-empty leaf is a pattern.

This yields the *complete* pattern set: every feasible pattern exists in it,
so the model below can represent every feasible plan. That completeness is
what lets Saw prove optimality and infeasibility. The cost is that the set can
grow exponentially with the number and quantities of compatible demands,
which is why enumeration is bounded by `pattern_limit` and by the time budget.
If either stops enumeration, the partial set is discarded rather than
optimized, because optimizing a truncated set could produce a false
optimality or infeasibility claim.

## 4. The integer program

Each pattern `p` gets an integer variable `x_p >= 0`: how many stock pieces
are cut with it, bounded above by its stock record's quantity. The
constraints are:

- **Exact demand.** For every demand, the pieces produced by all used patterns
  equal the requested quantity exactly.
- **Bounded stock.** For every stock record, the pieces consumed by its
  patterns do not exceed its inventory.

The exact formulas are in sections 4 and 5 of the problem specification.

## 5. Three lexicographic stages

The objective is the tuple (stock pieces, total leftover, distinct patterns),
compared lexicographically. Saw solves it as three successive CBC runs on the
same model:

1. **Primary.** Minimize the number of stock pieces `B = sum x_p`.
2. **Secondary.** Fix `B` to its proved optimum and minimize total leftover,
   the unused length summed over every consumed piece.
3. **Tertiary.** Also fix leftover to its proved optimum, add a binary
   indicator per pattern linked to `x_p`, and minimize how many distinct
   patterns are used.

Each later stage only runs after the earlier stage proved its optimum, and
each stage gets only the time left in the shared budget. Every reported
objective value is checked to be integral.

The outcome maps to a status as follows:

- All three stages proved optimal: `OPTIMAL`.
- CBC proved the primary model infeasible: `INFEASIBLE`.
- The deadline stopped a stage but a candidate from it or an earlier stage
  exists: that candidate is verified and returned as `FEASIBLE`, with
  termination reason `time_limit` and only the proved stages listed in
  `completed_objective_stages`.
- The deadline stopped work with no candidate: `LIMIT`.
- CBC failed or reported something unrecognized: `ERROR`. Failure is never
  translated into infeasibility.

## 6. Independent verification

Before any plan is returned, a verifier that shares no code with the
optimization model re-checks it against the original problem: stock and
demand IDs, material compatibility, that each pattern fits its stock length,
inventory bounds, exact production, and the recomputed objective tuple. A
candidate that fails is never returned as a normal result; Saw raises
`InternalVerificationError` instead.

Verification proves a plan is feasible and its numbers are right. It does not
prove optimality; that proof comes only from step 5 finishing all three
stages.

## Why this design

The exact backend favours correctness over scale. Enumerating every pattern
makes the model complete, so Saw never has to guess whether a better plan or
any plan exists. The price is that it is meant for small and medium-small
instances; [limitations.md](limitations.md) explains the boundaries. A
scalable column-generation backend is planned for a later release.
