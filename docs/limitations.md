# Saw v0.1.0 limitations

Saw v0.1.0 is a correctness-focused development release for small and
medium-small instances. It is not yet presented as production-ready.

## Enumeration and resource use

**The exact backend enumerates the complete bounded pattern set in memory
before optimization. Pattern count can grow exponentially with the number and
quantities of compatible demands. Saw v0.1.0 therefore provides no runtime or
memory guarantee for any particular input size.**

There is no reliable universal size threshold: material grouping, lengths,
quantities, and how many combinations fit each stock record all affect pattern
count. Callers should use `diagnostics.patterns_generated` when comparing runs,
but that value is available only after enumeration has completed.

`SolveOptions.time_limit_seconds` is passed to each CBC optimization stage. It
does not limit pattern enumeration, model construction, verification, or total
wall-clock time, and a three-stage solve may spend up to the configured solver
limit more than once. Treat it as a per-stage solver control, not as a hard
end-to-end deadline.

Although `SolveOptions` exposes `pattern_limit`, the v0.1.0 exact backend does
not implement it. Supplying one returns `UNSUPPORTED`; it does not truncate
enumeration. Safe problem-size, enumeration, and end-to-end resource controls
are roadmap work deferred to v0.2.0; they are not available in v0.1.0.

The units, defaults, disable/override behavior, and termination rules selected
for that v0.2.0 work are defined in the
[v0.2.0 resource-limit contract](resource-limits.md). That contract does not
retroactively change v0.1.0 behavior.

## Unsupported cutting and inventory semantics

v0.1.0 does not support:

- non-zero kerf, blade width, or end trim;
- fractional lengths, alternate units, or unit conversion;
- underproduction, surplus, or intentional overproduction;
- reusable remnants, offcut carry-over, or inventory returned by leftovers;
- material substitution, conversion, or compatibility hierarchies;
- unbounded or purchasable stock;
- cutting sequence, machine assignment, scheduling, or setup constraints; or
- two-dimensional or three-dimensional packing geometry.

A reported leftover is only unused length on a consumed stock piece. It has no
future inventory value in the v0.1.0 model.

## Unsupported objectives and interfaces

The objective is fixed to stock-piece count, then leftover, then distinct
pattern count. There are no caller-provided prices, weighted objectives,
custom tradeoffs, or alternative objective orderings.

The only supported interface is the Python library. v0.1.0 does not provide a
command-line interface, JSON input or output, versioned JSON schemas, web or
database integration, or company-specific inventory behavior. It also does
not include a column-generation or heuristic backend.

Several of those capabilities appear under v0.2.0 or later in the project
roadmap, but roadmap entries are future work and are not part of the v0.1.0 API
or guarantees.
