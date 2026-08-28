"""PuLP/CBC-specific integration for optimization backends.

This module is the only place where Saw translates PuLP statuses or creates a
CBC solver.  Domain-level backends can build their models separately and pass
the resulting ``LpProblem`` to :class:`PulpCbcAdapter`.
"""

from typing import Final

import pulp
from pulp import constants as pulp_constants

from saw.backends.base import BackendAvailability, BackendOptions
from saw.exceptions import BackendUnavailableError
from saw.models import BackendStatus


class PulpAdapterError(RuntimeError):
    """Raised when PuLP/CBC fails unexpectedly at the adapter boundary."""


_PULP_STATUS_TRANSLATION: Final[dict[int, BackendStatus]] = {
    pulp_constants.LpStatusOptimal: BackendStatus.OPTIMAL,
    pulp_constants.LpStatusNotSolved: BackendStatus.NOT_SOLVED,
    pulp_constants.LpStatusInfeasible: BackendStatus.INFEASIBLE,
    pulp_constants.LpStatusUnbounded: BackendStatus.UNBOUNDED,
    pulp_constants.LpStatusUndefined: BackendStatus.UNKNOWN,
}


def _packaged_cbc_path() -> str | None:
    """Return the CBC path supplied by PuLP's optional ``cbc`` extra."""

    try:
        import cbcbox
    except ImportError:
        return None

    return cbcbox.cbc_bin_path()


def _new_cbc_solver(options: BackendOptions) -> pulp.COIN_CMD:
    """Construct CBC, preferring the executable from the declared extra."""

    return pulp.COIN_CMD(
        msg=False,
        timeLimit=options.time_limit_seconds,
        path=_packaged_cbc_path(),
    )


def translate_pulp_status(
    status: int,
    solution_status: int | None = None,
) -> BackendStatus:
    """Translate PuLP model and solution statuses into Saw's vocabulary.

    CBC can stop at a limit with a usable integer incumbent.  PuLP 3.x reports
    that combination as an ``Optimal`` model status paired with an ``Integer
    Feasible`` solution status.  It is only feasible, not proven optimal.
    Unknown values remain unknown rather than acquiring a stronger claim.
    """

    if (
        status
        in {
            pulp_constants.LpStatusOptimal,
            pulp_constants.LpStatusNotSolved,
        }
        and solution_status == pulp_constants.LpSolutionIntegerFeasible
    ):
        return BackendStatus.FEASIBLE

    return _PULP_STATUS_TRANSLATION.get(status, BackendStatus.UNKNOWN)


class PulpCbcAdapter:
    """Own construction, availability checks, and execution for PuLP/CBC."""

    backend_id: Final[str] = "pulp_cbc"

    def check_availability(self) -> BackendAvailability:
        """Report whether PuLP can find an executable CBC solver."""

        try:
            solver = _new_cbc_solver(BackendOptions())
            available = bool(solver.available())
        except Exception as exc:
            return BackendAvailability(
                available=False,
                reason=(
                    "PuLP/CBC availability check failed with "
                    f"{type(exc).__name__}: {exc}"
                ),
                backend_version=pulp.__version__,
            )

        if not available:
            return BackendAvailability(
                available=False,
                reason=(
                    "PuLP is installed, but no executable CBC solver was "
                    "found. Install Saw with its declared PuLP CBC extra or "
                    "put cbc on PATH."
                ),
                backend_version=pulp.__version__,
            )

        return BackendAvailability(
            available=True,
            backend_version=pulp.__version__,
        )

    def create_solver(self, options: BackendOptions) -> pulp.COIN_CMD:
        """Create the configured command-line CBC solver."""

        try:
            return _new_cbc_solver(options)
        except Exception as exc:
            raise PulpAdapterError(
                "PuLP/CBC solver construction failed."
            ) from exc

    def solve(
        self,
        model: pulp.LpProblem,
        options: BackendOptions,
    ) -> BackendStatus:
        """Solve a prepared PuLP model and return its translated status."""

        solver = self.create_solver(options)

        try:
            available = bool(solver.available())
        except Exception as exc:
            raise PulpAdapterError(
                "PuLP/CBC availability check failed before solving."
            ) from exc

        if not available:
            raise BackendUnavailableError(
                "PuLP is installed, but no executable CBC solver was found."
            )

        try:
            raw_status = model.solve(solver)
        except Exception as exc:
            raise PulpAdapterError("PuLP/CBC solve failed.") from exc

        return translate_pulp_status(
            raw_status,
            getattr(model, "sol_status", None),
        )
