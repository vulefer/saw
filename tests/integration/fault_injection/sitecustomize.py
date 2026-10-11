"""Fault injection for end-to-end tests of the installed ``saw`` command.

``tests/integration/test_cli_end_to_end.py`` puts this directory on
``PYTHONPATH`` and names a fault in ``SAW_TEST_FAULT``; Python's ``site``
module imports this file at interpreter startup, before the console script
runs. It swaps the backend underneath the real ``solve()`` exactly the way
the in-process tests do, so outcomes that no real problem can trigger on
demand (an unavailable CBC, a corrupted candidate, an interrupt) still run
through the real process: entry point, streams, and exit status.

Nothing here is imported unless ``SAW_TEST_FAULT`` is set.
"""

from __future__ import annotations

import os
import sys


def _install(fault: str) -> None:
    from saw.backends.base import (
        BackendAvailability,
        BackendCapabilities,
        BackendSupport,
        UnverifiedBackendResult,
        UnverifiedCandidate,
    )
    from saw.backends.exact_enumerated.backend import ExactEnumeratedBackend
    from saw.exceptions import BackendUnavailableError
    from saw.models import (
        BackendStatus,
        CutPattern,
        Diagnostics,
        ObjectiveValue,
        TerminationReason,
    )
    from saw.patterns import Pattern
    import saw.solve

    if fault == "cbc-missing":
        # The real backend and PuLP adapter; only the CBC lookup fails.
        class _MissingCbc:
            def available(self) -> bool:
                return False

        import saw.backends.pulp_backend

        saw.backends.pulp_backend._new_cbc_solver = lambda options: _MissingCbc()
        return

    # The optimal plan for examples/json/problem-v1-valid.json, and a plan
    # that cuts 6 + 6 from a 10-long stock.
    valid = CutPattern(Pattern("stock-10", {"demand-6": 1, "demand-4": 1}), 1)
    corrupted = CutPattern(Pattern("stock-10", {"demand-6": 2}), 1)

    def result(
        status: BackendStatus,
        reason: TerminationReason,
        pattern: CutPattern | None,
    ) -> UnverifiedBackendResult:
        return UnverifiedBackendResult(
            candidate=(
                None
                if pattern is None
                else UnverifiedCandidate((pattern,), ObjectiveValue(1, 0, 1))
            ),
            diagnostics=Diagnostics(
                termination_reason=reason,
                backend_status=status,
                elapsed_time=0.0,
                patterns_generated=1,
                iterations=1,
                completed_objective_stages=(),
                lower_bound=None,
                upper_bound=None,
            ),
        )

    real_backend = ExactEnumeratedBackend()
    outcomes = {
        "feasible": result(
            BackendStatus.FEASIBLE,
            TerminationReason.TIME_LIMIT,
            valid,
        ),
        "limit-with-incumbent": result(
            BackendStatus.LIMIT_REACHED,
            TerminationReason.TIME_LIMIT,
            valid,
        ),
        "backend-failure": result(
            BackendStatus.FAILED,
            TerminationReason.BACKEND_FAILURE,
            None,
        ),
        "corrupted-candidate": result(
            BackendStatus.OPTIMAL,
            TerminationReason.COMPLETED,
            corrupted,
        ),
    }
    raised = {
        "cbc-lost-mid-solve": lambda: BackendUnavailableError(
            "CBC stopped being available while solving."
        ),
        "internal-exception": lambda: RuntimeError("injected fault"),
        "keyboard-interrupt": KeyboardInterrupt,
    }
    if fault not in outcomes and fault not in raised and fault != "stray-print":
        raise SystemExit(f"sitecustomize: unknown SAW_TEST_FAULT {fault!r}")

    class _FaultyBackend:
        backend_id = "exact_enumerated"

        def capabilities(self) -> BackendCapabilities:
            return real_backend.capabilities()

        def check_availability(self) -> BackendAvailability:
            return BackendAvailability(available=True)

        def check_support(self, problem, options) -> BackendSupport:
            return BackendSupport(supported=True)

        def solve(self, problem, options) -> UnverifiedBackendResult:
            if fault == "stray-print":
                print("stray backend log line")
                return real_backend.solve(problem, options)
            if fault in raised:
                raise raised[fault]()
            return outcomes[fault]

    # Through sys.modules: the attribute saw.solve is the solve() function,
    # which the package re-exports under the submodule's name.
    sys.modules["saw.solve"]._BACKENDS = {"exact_enumerated": _FaultyBackend()}


_FAULT = os.environ.get("SAW_TEST_FAULT")
if _FAULT:
    _install(_FAULT)
