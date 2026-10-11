# Saw v0.1.0 problem specification

> **v0.2.0:** this mathematical problem is unchanged in v0.2.0, and this
> document remains its authoritative definition. v0.2.0 adds the command-line
> interface and JSON formats ([cli.md](cli.md), [formats.md](formats.md)) and
> resource limits ([resource-limits.md](resource-limits.md)) around it, so the
> interface items listed as not part of v0.1.0 in section 1.1 are now
> provided. The cutting, inventory, and objective limitations still apply.

This document is the authoritative definition of the mathematical problem
supported by Saw v0.1.0. Implementations, tests, and other documentation must
conform to it. If another document disagrees with this one about v0.1.0 problem
semantics, this document takes precedence.

## 1. Scope

Saw v0.1.0 solves a bounded, one-dimensional cutting-stock problem through its
Python library interface. A problem may contain:

- any finite number of stock records;
- any finite number of demand records;
- multiple stock lengths;
- a separate positive inventory quantity for every stock record; and
- multiple independent materials, identified by generic material IDs.

All lengths are positive integers measured in millimetres (`mm`). Saw does not
convert units or accept fractional lengths in v0.1.0.

Production is **exact**: every requested demand quantity must be produced,
neither less nor more. Kerf is **zero**: cutting consumes no width beyond the
requested piece lengths. There is also no end trim or other process loss.

### 1.1 Deliberate limitations

The following are not part of v0.1.0:

- command-line (CLI) input or output;
- JSON input, output, or schemas;
- reusable remnants or carry-over inventory;
- surplus or overproduction;
- non-zero kerf or end trim;
- caller-provided stock prices or weighted objectives;
- column generation;
- cutting order, machine scheduling, or two-dimensional geometry; and
- web, database, or company-specific inventory behavior.

A leftover in v0.1.0 is only a reported unused length on a consumed stock
piece. It cannot be returned to inventory or used by another problem.

The v0.1.0 exact backend constructs the complete set of patterns. This is
finite for every valid v0.1.0 input, but its size can grow exponentially. The
release is intended for small and medium-small instances and makes no runtime
or memory guarantee for a particular input size.

## 2. Input model

Let:

- `S` be the finite set of stock records; and
- `D` be the finite set of demand records.

The order in which records are supplied has no mathematical meaning.

### 2.1 Identifiers and unit

Every stock ID, demand ID, and material ID is a non-empty string. String
identity is exact and case-sensitive; implementations do not trim, normalize,
or infer identity from display text.

Stock IDs are unique within `S`, and demand IDs are unique within `D`. Stock
and demand IDs occupy separate namespaces, so a stock and a demand may use the
same string. A material ID is a value used for equality comparison; it does not
refer to a separate material record.

The problem unit is the literal string `mm`. Every stock length and demand
length uses this one unit. Any other unit is invalid in v0.1.0.

### 2.2 Stock records

Each stock record `s` in `S` is the tuple

```text
(stock_id_s, material_id_s, L_s, Q_s)
```

where:

- `stock_id_s` is a non-empty string;
- `material_id_s` is a non-empty string;
- `L_s` is the stock length and is an integer in `Z_{>0}`; and
- `Q_s` is the number of physically available stock pieces and is an integer
  in `Z_{>0}`.

`Q_s` is a hard upper bound, not a purchasing suggestion. Two stock records
with equal lengths and materials remain distinct inventory pools when their
stock IDs differ.

### 2.3 Demand records

Each demand record `d` in `D` is the tuple

```text
(demand_id_d, material_id_d, l_d, q_d)
```

where:

- `demand_id_d` is a non-empty string;
- `material_id_d` is a non-empty string;
- `l_d` is the required piece length and is an integer in `Z_{>0}`; and
- `q_d` is the exact required number of pieces and is an integer in
  `Z_{>0}`.

Mathematical integers in this specification exclude Boolean values. There is
no implicit rounding, coercion from floating point, or unit conversion.

### 2.4 Empty collections

Empty stock and demand collections are valid inputs:

- If `D` is empty, the empty plan is feasible and optimal with objective value
  `(0, 0, 0)`, whether or not stock exists.
- If `D` is non-empty and `S` is empty, the problem is valid but infeasible.

### 2.5 Material compatibility

A demand `d` may be cut from stock `s` if and only if

```text
material_id_d == material_id_s
```

No substitution, hierarchy, conversion, or cross-material cutting is allowed.
A demand with no compatible stock is not malformed input; it makes the problem
infeasible unless its exact quantity can somehow be produced, which is
impossible under this model.

### 2.6 Invalid input versus infeasibility

An input is **invalid** when it fails the data contract, for example when an ID
is empty or duplicated in its namespace, a numeric field is not a positive
non-Boolean integer, or the unit is not `mm`. Invalid input is rejected before
optimization with a validation exception. It does not receive an optimization
status.

A valid input is **infeasible** when it satisfies the data contract but no plan
satisfies all constraints. Examples include insufficient inventory, a demand
piece longer than every compatible stock length, and a demand whose material
has no stock. These cases must reach the solver and may be returned as
`INFEASIBLE` only when infeasibility is proven.

### 2.7 Validation reporting

Validation is deterministic and fail-fast. Saw reports the first error in this
order: stock records in input order, demand records in input order, duplicate
stock IDs, then duplicate demand IDs. A primitive-field error identifies its
record kind, record index, and field. A duplicate-ID error reports every
conflicting ID in that namespace. Every validation exception also carries a
stable machine-readable error code.

## 3. Cutting patterns

A cutting pattern `p` consists of:

- exactly one `stock_id`, identifying a record `s(p)` in `S`; and
- a finite mapping from demand IDs to piece counts `a[p,d]`.

For every `d` in `D`, `a[p,d]` is an integer satisfying

```text
0 <= a[p,d] <= q_d.
```

Zero-count entries are omitted from the canonical mapping. Piece order within
a stock length is not represented and has no effect.

A pattern is feasible if and only if all of the following hold:

1. Every referenced demand ID exists in `D`.
2. At least one piece is present:

   ```text
   sum(d in D) a[p,d] > 0.
   ```

3. Every included demand has the same material as its stock:

   ```text
   a[p,d] > 0 implies material_id_d == material_id_s(p).
   ```

4. With zero kerf, the pieces fit within the stock length:

   ```text
   used_length(p) = sum(d in D) l_d * a[p,d] <= L_s(p).
   ```

The leftover length of one use of a pattern is therefore

```text
leftover(p) = L_s(p) - used_length(p).
```

Kerf, trim, and reusable remainder do not appear in either equation.

### 3.1 Pattern identity

Two patterns are identical if and only if they have the same stock ID and the
same demand-ID-to-positive-count mapping. Mapping iteration order and explicit
zero entries do not affect identity. Consequently, patterns tied to different
stock IDs are distinct even when the stock records and piece counts otherwise
match.

The complete v0.1.0 pattern set `P` contains every feasible, non-empty pattern
for every stock record. The per-demand upper bounds make `P` finite.

## 4. Complete-plan feasibility

For every pattern `p` in `P`, let `x_p` be its repetition count:

```text
x_p in Z_{>=0}.
```

Public results list only patterns for which `x_p > 0`; the repetition count is
separate from the piece counts within a pattern. Duplicate entries with the
same pattern identity are not part of the canonical result: their repetitions
are aggregated.

A complete plan is feasible if and only if every used pattern is feasible and
the following constraints hold.

### 4.1 Bounded stock

For each stock record `s`:

```text
sum(p in P where s(p) = s) x_p <= Q_s.
```

Unused stock is allowed. Inventory is enforced separately by stock ID, even
when multiple stock records share a length or material.

### 4.2 Exact production

For each demand record `d`:

```text
sum(p in P) a[p,d] * x_p = q_d.
```

The equality prohibits both underproduction and surplus. Pieces and production
totals are always associated with stable demand IDs, never only with array
positions.

## 5. Lexicographic objective

Among all feasible plans, v0.1.0 minimizes the following tuple in strict
lexicographic order:

```text
(stock_piece_count, total_leftover, distinct_pattern_count)
```

There is no weighted sum and no caller-configurable tradeoff between stages.

### 5.1 Primary: stock-piece count

```text
B(x) = sum(p in P) x_p
```

This counts physical stock pieces consumed. Stock length, material, price, and
record identity do not change the contribution: every consumed stock piece
contributes one.

### 5.2 Secondary: total leftover length

Subject to `B(x)` equaling its proven minimum `B*`, minimize

```text
W(x) = sum(p in P) x_p * leftover(p)
     = sum(p in P) x_p *
       (L_s(p) - sum(d in D) l_d * a[p,d]).
```

`W(x)` is measured in millimetres and counts the unused length on every
consumed stock piece. It is not reusable inventory.

### 5.3 Tertiary: distinct positive-use patterns

Subject to both `B(x) = B*` and `W(x) = W*`, minimize

```text
K(x) = cardinality({p in P | x_p > 0}).
```

Distinctness uses the pattern identity from section 3.1. Repeating one pattern
many times contributes one to `K`; using patterns with identical piece counts
but different stock IDs contributes once for each stock ID used.

### 5.4 Meaning of optimality

A plan is lexicographically optimal only when no feasible plan has:

1. a smaller `B`; or
2. the same `B` and a smaller `W`; or
3. the same `B` and `W` and a smaller `K`.

An implementation may solve these as three stages, but each later stage must
constrain every earlier objective to its proven optimum. The three formulas
above use only the input records, pattern mappings, and repetition counts, so
an independent verifier can recompute every reported component.

## 6. Result statuses and proof rules

Statuses state what Saw established, not merely what an optimization library
reported.

| Status | Precise meaning |
| --- | --- |
| `OPTIMAL` | A returned plan was independently verified as feasible, its reported objective components were independently recomputed, and a complete method proved that no lexicographically better feasible plan exists. |
| `FEASIBLE` | A returned plan was independently verified as feasible and its reported objective components were independently recomputed, but global lexicographic optimality was not proven. |
| `INFEASIBLE` | The input was valid and a complete method proved that no feasible plan exists. No plan is returned. |
| `LIMIT` | A time, node, iteration, pattern, size, or other declared resource limit stopped the operation before an optimality or infeasibility proof. A plan may accompany the result only if it was independently verified; otherwise no plan is returned. |
| `UNSUPPORTED` | The input is valid under the public problem contract, but the selected backend cannot solve that valid variant or magnitude. This makes no claim about feasibility. |
| `ERROR` | An internal component or backend failed, or returned an unrecognized outcome, so Saw can make no feasibility, infeasibility, or optimality claim. |

The following rules are mandatory:

- Solver failure, an unknown backend status, exhaustion without a proof, or the
  absence of a newly found pattern cannot be translated to `INFEASIBLE`.
- A feasible incumbent without a global proof cannot be labeled `OPTIMAL`.
- `LIMIT`, `UNSUPPORTED`, and `ERROR` make no mathematical claim beyond any
  separately verified incumbent attached to a `LIMIT` result.
- Expected optimization outcomes, including proven infeasibility and reaching
  a declared limit, are structured results rather than exceptions.
- Invalid Python input is a validation exception, backend unavailability may
  use a documented backend exception, and rejection of a backend candidate by
  the independent verifier is an internal verification exception. An invalid
  candidate is never exposed as a normal solution.

### 6.1 Verification is not an optimality proof

Independent verification checks a candidate directly against sections 3 and 4
and recomputes the objective formulas in section 5. Passing verification proves
that the returned plan and metrics are internally valid for the supplied
problem. It does **not** prove that a better feasible plan does not exist.

`OPTIMAL` additionally requires a valid optimality proof from a complete
optimization method. `INFEASIBLE` similarly requires a valid infeasibility
proof; it cannot be inferred from failure to find a plan. The complete-pattern
exact backend may supply those proofs only after it has considered the complete
v0.1.0 formulation and terminated conclusively.

## 7. Minimal example

For one stock record with length `10`, quantity `1`, and material `steel`, and
two demands of the same material requiring one piece of length `6` and one of
length `4`, the mixed pattern

```text
stock_id: stock-10
pieces: {demand-6: 1, demand-4: 1}
repetitions: 1
```

is feasible. It uses length `10`, has leftover `0`, respects the one-piece
stock bound, and produces both demands exactly. Its objective tuple is
`(1, 0, 1)`.
