# Saw: Project Plan, Version Roadmap, and Repository Organization

Saw is a correctness-first solver for one-dimensional cutting-stock problems. Its central promise is:

> Every returned plan is independently verified, and every claimed guarantee is backed by evidence.

This document defines the planned release sequence, development priorities, architecture, repository structure, and organizational rules for the project.

## Guiding principles

1. **Specify the problem before implementing the optimizer.** Inputs, constraints, objectives, units, and guarantees must be mathematically unambiguous.
2. **Correctness comes before scalability.** Small problems should be solved exactly before larger problems are solved heuristically.
3. **Verify every solution independently.** Solver output is not trusted merely because the optimization backend accepts it.
4. **Use honest statuses.** `FEASIBLE`, `INFEASIBLE`, `LIMIT`, and `OPTIMAL` have different meanings and must never be conflated.
5. **Keep the solver independent.** Databases, HTTP APIs, authentication, billing, and company-specific inventory concepts do not belong in the core package.
6. **Make performance claims only from reproducible benchmarks.** Correctness and validity are measured before speed.
7. **Fail safely.** Invalid or oversized problems must terminate predictably with useful diagnostics.

## Initial problem definition

The first implementation should support:

- Positive integer lengths in one documented unit, normally millimetres.
- Positive integer demand quantities.
- Bounded quantities of stock.
- Multiple stock lengths.
- Multiple independent materials, identified by a generic `material_id`.
- Exact production by default.
- Zero kerf in the earliest versions, clearly documented.
- A lexicographic objective:
  1. satisfy all demand;
  2. minimize total stock cost, defaulting to stock-piece count;
  3. minimize leftover length;
  4. minimize the number of distinct cutting patterns.

The first implementation should not support reusable remnants, cutting sequence, two-dimensional geometry, machine scheduling, or undocumented weighted combinations of competing objectives.

## Version roadmap

### v0.1.0: Correct foundation

**Theme:** Make small problems unquestionably correct.

#### Goals

- Write the mathematical problem specification.
- Define generic, immutable `Stock`, `Demand`, `Pattern`, `Problem`, and `Solution` models.
- Implement complete input validation.
- Implement an independent solution verifier.
- Implement bounded pattern enumeration.
- Implement a complete-pattern exact integer solver for supported problem sizes.
- Support bounded stock, multiple stock lengths, and independent materials.
- Return structured statuses and diagnostics.
- Add unit, regression, oracle, and exhaustive small-instance tests.

#### Deliberate limitations

- Zero kerf.
- Exact demand only.
- No reusable remnants.
- Small and medium-small instances only.
- Python library interface only.
- Development release; not yet marketed as production-ready.

#### Release gate

- Stock length `10`, quantity `1`, with demands `6 x 1` and `4 x 1`, returns the valid mixed pattern `{6: 1, 4: 1}`.
- Every returned solution passes independent verification.
- Exhaustively generated small cases match an independent oracle.
- Public results identify pieces by demand ID, never only by positional arrays.
- Invalid inputs fail with documented validation errors.

### v0.2.0: Usable developer release

**Theme:** Make the correct solver straightforward to install and use.

#### Goals

- Add standard `pyproject.toml` packaging.
- Add a JSON command-line interface.
- Publish versioned JSON input and output schemas.
- Add safe time, problem-size, and enumeration limits.
- Add structured errors and diagnostics.
- Add a complete README, examples, algorithm explanation, and limitations.
- Add continuous integration across supported Python versions and operating systems.
- Add the license, contribution guide, code of conduct, changelog, and security policy.
- Constrain direct dependencies and create a reproducible development environment.

#### Release gate

- An unfamiliar developer can install Saw and solve a documented example in under ten minutes.
- Clean installations pass on Linux, Windows, and macOS.
- Invalid input, infeasibility, missing solver, and resource limits produce documented results.
- Package building and testing are reproducible.
- Every command-line result uses the same validation and verification path as the Python library.

This is the earliest release that should be made public, labeled **alpha**.

### v0.3.0: Scalable solver

**Theme:** Handle larger problems without sacrificing honesty.

#### Goals

- Add a column-generation backend.
- Start with a guaranteed-feasible restricted master using artificial shortage columns or feasibility pricing.
- Derive reduced costs from the exact documented objective.
- Include all relevant demand and stock-capacity dual terms.
- Handle duplicate columns and numerical tolerances explicitly.
- Add time, iteration, pattern, and backend limits.
- Normalize inputs and define deterministic tie-breaking where practical.
- Compare column-generation results automatically against the exact v0.1 backend.
- Report bounds and optimality gaps only when mathematically valid.
- Return `FEASIBLE`, rather than `OPTIMAL`, for restricted integer solutions without a global proof.

#### Release gate

- Every supported small problem agrees with the exact backend on feasibility.
- Every returned plan passes the independent verifier.
- Reaching a limit never becomes false optimality or false infeasibility.
- Diagnostics report iterations, generated patterns, termination reason, elapsed time, and valid bounds.
- Larger benchmark cases finish within documented resource limits.

### v0.4.0: Real cutting semantics

**Theme:** Support practical manufacturing constraints with precise definitions.

#### Goals

- Add a mathematically defined kerf model.
- Add configurable end-trim allowance.
- Support exact-demand and allowed-surplus modes.
- Support stock cost as an alternative to stock-piece count.
- Distinguish scrap from reusable remainder.
- Report required, produced, and surplus quantities by demand ID.
- Add configurable lexicographic objective policies.
- Add unit-aware validation, examples, and output metrics.

#### Release gate

- Every new variant has a written mathematical specification.
- Kerf and trim boundary tests pass.
- Objective priorities cannot reverse because of input scale or arbitrary penalty weights.
- All output metrics are independently recomputed by the verifier.
- Existing zero-kerf behavior remains backward-compatible.

### v0.5.0: Public beta

**Theme:** Establish credible reliability and performance evidence.

#### Goals

- Run established BPPLIB instances where the problem definition matches.
- Add generated benchmarks for bounded, heterogeneous stock.
- Compare results with independent complete-pattern and item-assignment models.
- Measure validity, objective value, lower bounds, runtime, CPU time, peak memory, pattern count, and termination status.
- Add randomized property testing, metamorphic testing, fuzzing, and failure injection.
- Profile and optimize only demonstrated bottlenecks.
- Publish benchmark inputs, commands, hardware details, dependency versions, and raw results.
- Gather feedback from developers and manufacturing users.

#### Release gate

- There are no known invalid-solution or false-status defects.
- Benchmark results are reproducible from documented commands.
- Performance claims are limited to measured configurations.
- Input-order permutation does not change feasibility or objective value.
- Several unfamiliar users have successfully solved their own problems.

### v1.0.0: Stable library

**Theme:** Provide a dependable public contract.

#### Goals

- Stabilize the Python API, CLI, and JSON formats.
- Define semantic-versioning and deprecation policies.
- Document guarantees, non-guarantees, supported variants, and practical limits.
- Complete security, governance, contribution, and release processes.
- Publish checksummed or signed release artifacts.
- Maintain a tested platform and Python compatibility matrix.
- Provide migration guidance from earlier Saw releases and the legacy prototype.
- Resolve release-blocking issues discovered during beta use.

#### Release gate

- No unresolved critical correctness, security, ownership, or licensing issues exist.
- Every supported backend has documented status guarantees.
- Tests, benchmarks, packaging, documentation, and examples pass from the release tag.
- The maintainers are prepared to preserve the public API throughout v1.x.
- A clean-room user can reproduce the release and its documented examples.

### v1.1 and later: Adoption and advanced optimization

Possible additions should be driven by user demand and benchmark evidence:

- Additional solver backends.
- Exact branch-and-price.
- Warm starts and reusable pattern pools.
- Improved deterministic tie-breaking.
- Cut-count and setup-change objectives.
- Batch solving.
- Solution visualization.
- Common import and export adapters.
- Performance improvements supported by published measurements.

### Hosted service: separate project

The hosted API should not become v2 of the library. It should be a separate application that depends on Saw.

Possible hosted-service goals include:

- A versioned HTTP API.
- Authentication and authorization.
- Request, payload, concurrency, and rate limits.
- Isolated solver workers.
- Deadlines, cancellation, and asynchronous jobs.
- Privacy-safe logs and observability.
- Usage quotas and optional billing.
- Private database and inventory adapters.
- A constrained public demo.

The Saw library must remain completely usable without the hosted service.

## Development priorities

Focus on these areas in order.

### 1. Precise semantics

Define exactly:

- Exact versus surplus production.
- Stock count versus monetary or caller-provided cost.
- The priority of waste and pattern-count minimization.
- Units and integer scaling.
- Kerf, trim, scrap, and reusable-remnant behavior.
- The conditions under which optimality or infeasibility is proven.

### 2. Independent verification

Treat every solver backend as potentially incorrect. The verifier must independently recompute:

- Pattern capacity.
- Material compatibility.
- Stock consumption and inventory limits.
- Required, produced, and surplus quantities.
- Waste and remainder.
- Every objective component.
- Status and proof consistency.

No backend result should be returned to a caller unless verification passes.

### 3. Correctness before scalability

Implement the complete-pattern exact solver before column generation. Preserve it permanently as a test oracle for scalable backends.

### 4. Honest statuses

Use the following result statuses consistently:

- `OPTIMAL`: optimality was proven.
- `FEASIBLE`: a verified solution was found without an optimality proof.
- `INFEASIBLE`: infeasibility was proven.
- `LIMIT`: a time, node, iteration, pattern, or size limit was reached.
- `UNSUPPORTED`: the problem is valid but outside the selected backend's capabilities.
- `ERROR`: an internal or backend failure occurred.

Never translate `LIMIT`, solver failure, or lack of a newly generated column into `OPTIMAL` or `INFEASIBLE`.

### 5. A narrow public API

The core public concepts should remain small:

- `Problem`
- `Stock`
- `Demand`
- `SolveOptions`
- `Solution`
- `CutPattern`
- `VerificationReport`
- `solve(...)`
- `verify_solution(...)`

Databases, HTTP frameworks, authentication, billing, customer records, RAL codes, and company-specific inventory fields belong in adapters or separate projects.

### 6. Tests as algorithm infrastructure

Prioritize:

- Input-validation tests.
- Pattern-feasibility tests.
- Verifier tests using deliberately corrupted solutions.
- Exhaustive small-instance tests.
- Known regression cases.
- Randomized property tests.
- Input permutation and unit-scaling tests.
- Timeout, missing-backend, and solver-failure tests.
- Cross-backend comparison tests.
- Performance regression tests with generous, environment-aware thresholds.

### 7. Safe failure and resource controls

Every backend should support or enforce:

- Time limits.
- Problem-size limits.
- Pattern-count limits.
- Iteration and node limits where applicable.
- Cancellation where supported.
- Numeric magnitude validation.
- A useful termination reason and partial incumbent when safe.

### 8. Reproducibility

- Define supported Python versions.
- Constrain runtime dependencies.
- Lock development, CI, and benchmark environments.
- Record optimization backend versions.
- Publish raw benchmark inputs and results.
- Record hardware, process count, seeds, and time limits.

### 9. Documentation of limitations

For every backend and feature, document:

- The exact problem variant.
- Exactness or heuristic guarantee.
- Supported input range.
- Termination behavior.
- Practical limits.
- Unsupported manufacturing constraints.

### 10. Maintainable boundaries

Keep models, validation, objective calculation, pattern generation, solver orchestration, backend execution, verification, serialization, and user interfaces independently testable.

## Work to defer

Do not prioritize these before the mathematical and testing foundation is stable:

- Web or desktop UI design.
- Hosted demos.
- Database integration.
- Authentication and billing.
- Microservices.
- Many interchangeable solver backends.
- Machine learning.
- Aggressive performance optimization without profiling.
- Two-dimensional cutting.
- Every possible business objective.
- Claims of superiority over established solvers or services.

## Proposed repository structure

```text
saw/
├── pyproject.toml
├── README.md
├── PROJECT_PLAN.md
├── LICENSE
├── NOTICE
├── CHANGELOG.md
├── CONTRIBUTING.md
├── CODE_OF_CONDUCT.md
├── SECURITY.md
├── src/
│   └── saw/
│       ├── __init__.py
│       ├── models.py
│       ├── validation.py
│       ├── objectives.py
│       ├── patterns.py
│       ├── verification.py
│       ├── results.py
│       ├── solve.py
│       ├── exceptions.py
│       ├── serialization.py
│       ├── cli.py
│       └── backends/
│           ├── __init__.py
│           ├── base.py
│           ├── exact_enumerated/
│           │   ├── __init__.py
│           │   ├── backend.py
│           │   ├── model.py
│           │   ├── stages.py
│           │   └── output.py
│           ├── pulp_backend.py
│           └── column_generation.py
├── tests/
│   ├── unit/
│   ├── regression/
│   ├── property/
│   ├── oracle/
│   ├── integration/
│   └── compatibility/
├── benchmarks/
│   ├── README.md
│   ├── run.py
│   ├── instances/
│   └── results/
├── examples/
│   ├── basic.py
│   ├── multiple_stock.py
│   ├── multiple_materials.py
│   └── request.json
├── docs/
│   ├── problem-spec.md
│   ├── objectives.md
│   ├── algorithms.md
│   ├── guarantees.md
│   ├── formats.md
│   └── limitations.md
└── .github/
    ├── workflows/
    │   ├── ci.yml
    │   ├── benchmarks.yml
    │   └── release.yml
    ├── ISSUE_TEMPLATE/
    │   ├── bug.yml
    │   ├── invalid-solution.yml
    │   ├── performance.yml
    │   └── feature.yml
    └── pull_request_template.md
```

## Module responsibilities

### `models.py`

Contains immutable domain input models such as `Problem`, `Stock`, and `Demand`. It must not import optimization backends, HTTP frameworks, or databases.

### `validation.py`

Validates identifiers, units, numeric ranges, material compatibility, quantity rules, duplicate records, configured limits, and backend capability requirements.

### `objectives.py`

Defines named objective policies and their lexicographic ordering. It also independently calculates objective components from a verified solution.

### `patterns.py`

Contains the pattern representation, feasibility calculations, bounded pattern enumeration, and reusable pattern utilities. It must not decide whether a complete solution is optimal.

### `verification.py`

Independently verifies complete solutions. It should share domain models but avoid reusing backend formulation logic that could reproduce the same defect.

### `results.py`

Defines statuses, termination reasons, diagnostics, proof metadata, cut patterns, and final solution structures.

### `solve.py`

Provides the main `solve(...)` entry point. It validates the problem, selects a backend, invokes it, verifies its result, and returns the public solution.

### `exceptions.py`

Contains stable exceptions for invalid input, unavailable backends, serialization problems, and internal verification failures. Expected optimization outcomes such as infeasibility and time limits should normally be result statuses, not exceptions.

### `serialization.py`

Owns versioned JSON conversion and schemas. It must preserve IDs and avoid positional-only pattern formats.

### `cli.py`

Provides a thin command-line wrapper around the same validation, solving, serialization, and verification paths used by the library.

### `backends/base.py`

Defines the internal solver-backend protocol, capability reporting, configuration, raw result contract, and termination semantics.

### `backends/exact_enumerated/`

Implements the complete-pattern exact solver for supported sizes. It remains in the project permanently as a production option for small problems and as the reference oracle for other backends. Its internal modules are organized by responsibility:

- `backend.py` orchestrates the complete exact-solving pipeline.
- `model.py` builds pattern variables, constraints, and shared expressions.
- `stages.py` keeps the three lexicographic optimization stages and their result types together.
- `output.py` extracts candidates, builds diagnostics, and translates termination statuses.
- `__init__.py` preserves the public `saw.backends.exact_enumerated` import surface.

### `backends/column_generation.py`

Implements feasibility-aware column generation and restricted integer solving. Unless extended to a proven exact method, it must return `FEASIBLE` rather than `OPTIMAL` when no global proof exists.

### `backends/pulp_backend.py`

Contains the PuLP/CBC adapter, including solver construction, PuLP status translation, time-limit configuration, execution, and backend availability checks. Exact-solver model construction remains inside `backends/exact_enumerated/model.py`.

## Architectural flow

```text
Python API or CLI
        │
        ▼
Versioned parsing and validation
        │
        ▼
Backend selection
        │
        ├── Exact pattern-enumeration backend
        │
        └── Scalable column-generation backend
        │
        ▼
Raw backend result
        │
        ▼
Independent solution verification
        │
        ├── verification failed ──> internal error
        │
        └── verification passed
        │
        ▼
Public Solution with honest status and diagnostics
```

## Public model conventions

### Identifiers

- Stock, demand, and material IDs are stable strings.
- IDs must be unique within their category.
- Output patterns refer to demand IDs directly.
- Display names and business metadata never determine material compatibility.

### Numeric values

- Core lengths and quantities are integers.
- The selected unit is recorded with the problem or defined by the format version.
- Floating-point objective costs are isolated from capacity calculations.
- Numeric magnitude limits are validated before model construction.

### Result statuses

- Status values are stable public API values.
- Backend-specific statuses are translated in one place.
- A feasible incumbent may accompany `LIMIT` when it passes verification.
- Bounds and gaps are omitted unless they are valid for the actual formulation and termination state.

### Patterns

- Patterns identify stock and pieces by ID.
- Pattern feasibility is independently checkable.
- Repetition count is separate from per-pattern piece counts.
- Used length, kerf loss, trim, and remainder are reported separately when supported.

## Test organization

### `tests/unit`

Fast tests for models, validation, objectives, pattern feasibility, serialization, and verifier components.

### `tests/regression`

One named fixture per previously discovered defect. Every correctness bug must add a failing regression test before it is fixed.

### `tests/property`

Generated tests for invariants such as:

- No pattern exceeds stock capacity.
- No stock quantity is exceeded.
- Materials never mix.
- All reported totals recompute exactly.
- Input permutation preserves feasibility and objective value.
- Increasing inventory cannot make a feasible problem infeasible.
- Increasing demand cannot improve the objective under the same policy.

### `tests/oracle`

Independent exhaustive and alternative-formulation comparisons. Oracle code should not call production pattern generation or verification helpers that would make failures correlated.

### `tests/integration`

Tests involving the actual optimization backend, CLI, package installation, time limits, and serialization round trips.

### `tests/compatibility`

Tests for supported Python versions, operating systems, optional solver availability, and public format compatibility.

## Benchmark organization

Standard unlimited-identical-stock benchmarks and Saw's bounded heterogeneous-stock benchmarks must be reported separately.

Every benchmark run should record:

- Saw version and commit.
- Python and optimization-backend versions.
- Operating system and hardware.
- Process and thread counts.
- Time, node, iteration, and pattern limits.
- Random seeds where relevant.
- Feasibility and verification status.
- Objective components and valid bounds.
- Wall time, CPU time, and peak memory.
- Pattern and subproblem counts.
- Raw machine-readable results.

Correctness and validity come before runtime comparisons.

## Dependency organization

Use dependency groups rather than one service-wide requirements file:

- **Core:** domain models and any unavoidable runtime utilities.
- **Solver:** optimization modeler and supported solver integration.
- **CLI:** command-line and JSON-schema support if not already core.
- **Development:** formatter, linter, type checker, and test tools.
- **Benchmark:** profiling and benchmark reporting tools.
- **Documentation:** documentation-site tooling.

The core library must never depend on FastAPI, SQLAlchemy, PostgreSQL, authentication libraries, or hosted-service infrastructure.

## Documentation organization

- `README.md` explains installation, a minimal example, supported scope, and status guarantees.
- `PROJECT_PLAN.md` contains the roadmap and repository organization.
- `problem-spec.md` is the authoritative mathematical problem definition.
- `objectives.md` defines every supported objective and its ordering.
- `algorithms.md` explains backend approaches without overstating guarantees.
- `guarantees.md` defines statuses, proofs, bounds, and verification.
- `formats.md` documents Python and JSON contracts with versioning rules.
- `limitations.md` prominently lists unsupported constraints and practical limits.
- `CHANGELOG.md` records user-visible changes under semantic versioning.

## Contribution organization

- Require tests for behavioral changes.
- Require a regression test for every correctness defect.
- Require mathematical specification updates for new constraints or objectives.
- Require benchmark evidence for performance claims.
- Keep formatting and mechanical cleanup separate from algorithm changes.
- Record contributor licensing expectations in `CONTRIBUTING.md`.
- Consider Developer Certificate of Origin sign-off if appropriate for the project.
- Label invalid-solution reports as the highest-priority defect category.

Suggested issue categories:

- `correctness`
- `invalid-solution`
- `false-optimality`
- `false-infeasibility`
- `performance`
- `backend`
- `format`
- `documentation`
- `security`
- `new-variant`

## Versioning and compatibility policy

- Use semantic versioning.
- During `0.x`, document breaking API and format changes prominently.
- Version serialized JSON formats independently inside the payload.
- Do not silently change objective semantics in a minor or patch release.
- Patch releases fix defects without intentionally changing supported behavior.
- Minor releases add backward-compatible functionality where practical.
- After v1.0, breaking Python API, CLI, status, or JSON changes require a major release.
- Solver-result changes caused by bug fixes must be called out clearly in the changelog.

## Release process

For every release:

1. Confirm ownership, license, and third-party notices.
2. Run formatting, linting, typing, unit, property, integration, and compatibility checks.
3. Run the regression and exhaustive oracle suites.
4. Run the defined benchmark smoke set.
5. Build source and wheel artifacts from a clean checkout.
6. Install and test the built artifacts in clean environments.
7. Update documentation and changelog.
8. Tag the exact tested commit.
9. Publish checksums or signed artifacts.
10. Verify the published installation and minimal example.

## Definition of done for solver changes

A solver or formulation change is complete only when:

- Its mathematical behavior is specified.
- The implementation is isolated behind the appropriate boundary.
- Unit and regression tests cover the change.
- Small cases match the independent oracle.
- Returned solutions pass independent verification.
- Status and diagnostics remain truthful under limits and failures.
- Documentation states the guarantee and limitations.
- Any performance statement is supported by reproducible measurements.

## Immediate implementation order

1. Create the new Saw repository rather than rewriting the legacy service in place.
2. Add `problem-spec.md` and settle the v0.1 semantics.
3. Add domain models and validation.
4. Implement the independent solution verifier.
5. Implement bounded pattern enumeration.
6. Implement the complete-pattern exact backend.
7. Add exhaustive small-instance and regression tests.
8. Add the public `solve(...)` function and structured results.
9. Add packaging, documentation, CI, and the JSON CLI.
10. Begin scalable column generation only after the exact foundation is reliable.
