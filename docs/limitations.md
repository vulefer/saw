# Saw v0.2.0 limitations

Saw v0.2.0 is a correctness-focused alpha release for small and medium-small
instances. It is not yet presented as production-ready.

## Enumeration and resource use

**The exact backend enumerates the complete bounded pattern set in memory
before optimization. Pattern count can grow exponentially with the number and
quantities of compatible demands.** There is no reliable universal size
threshold: material grouping, lengths, quantities, and how many combinations
fit each stock record all affect pattern count.

v0.2.0 therefore guards every call with safe default limits, defined in the
[resource-limit contract](resource-limits.md):

- one end-to-end `time_limit_seconds` deadline (default 30 seconds) shared by
  validation, enumeration, model building, all three objective stages, and
  verification;
- `problem_size_limit` (default 200 stock and demand records);
- `numeric_magnitude_limit` (default 1,000,000 for every length and quantity);
  and
- `pattern_limit` (default 50,000 enumerated patterns).

A problem stopped by one of these limits receives a `LIMIT` result (or a
verified `FEASIBLE` incumbent after a time limit), never a claim of
infeasibility. Practical consequences:

- The limits are guardrails, not guarantees. Memory is not measured directly,
  so a problem under every default limit can still use substantial memory, and
  a solve may finish slightly after the deadline because deadline checks are
  cooperative.
- A problem stopped by `pattern_limit` or a size guard is not optimized at
  all. Saw never optimizes a truncated pattern set.
- Raising or disabling a limit (passing `None`) is allowed per call, but
  removes that protection, including the risk of excessive time or memory use
  that v0.1.0 had.

`diagnostics.patterns_generated` reports how many patterns were enumerated
and is useful for comparing runs and choosing a `pattern_limit`.

## Exact-backend numeric range

The exact backend hands its model to CBC through a model file that stores
every number with 13 significant digits, so larger integers would reach CBC
already rounded. The backend therefore reports `unsupported` (CLI exit code
`5`) for any problem whose total stock length (the sum of `length * quantity`
over all stock records) or largest demand quantity exceeds `10**12`, instead
of solving a rounded model. With the default `numeric_magnitude_limit` this
only affects very large inputs; disabling that limit does not lift this one.

## Unsupported cutting and inventory semantics

v0.2.0 solves the same problem as v0.1.0 and does not support:

- non-zero kerf, blade width, or end trim;
- fractional lengths, alternate units, or unit conversion;
- underproduction, surplus, or intentional overproduction;
- reusable remnants, offcut carry-over, or inventory returned by leftovers;
- material substitution, conversion, or compatibility hierarchies;
- unbounded or purchasable stock;
- cutting sequence, machine assignment, scheduling, or setup constraints; or
- two-dimensional or three-dimensional packing geometry.

A reported leftover is only unused length on a consumed stock piece. It has no
future inventory value in the model.

## Unsupported objectives and interfaces

The objective is fixed to stock-piece count, then leftover, then distinct
pattern count. There are no caller-provided prices, weighted objectives,
custom tradeoffs, or alternative objective orderings.

The supported interfaces are the Python library and the `saw` command with the
version 1 JSON formats ([cli.md](cli.md), [formats.md](formats.md)). v0.2.0
does not provide a web API, database integration, or company-specific
inventory behaviour, and it has no column-generation or heuristic backend, so
large instances are out of scope.

Supported Python versions and platforms are listed in
[compatibility.md](compatibility.md). Packages are not published to a public
index.

Items on the project roadmap for later releases are future work and are not
part of the v0.2.0 API or guarantees.
