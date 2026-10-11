# the only place that creates a cbc solver or translates pulp statuses
# backends build their own pulp model and pass it to PulpCbcAdapter to solve

from typing import Final

import pulp
from pulp import constants as pulp_constants

from saw.backends.base import BackendAvailability, BackendOptions
from saw.exceptions import BackendUnavailableError
from saw.models import BackendStatus


# raised when pulp/cbc fails unexpectedly
class PulpAdapterError(RuntimeError):
    pass


# translation of pulp statuses into saw's BackendStatus
_PULP_STATUS_TRANSLATION: Final[dict[int, BackendStatus]] = {
    pulp_constants.LpStatusOptimal: BackendStatus.OPTIMAL,
    pulp_constants.LpStatusNotSolved: BackendStatus.NOT_SOLVED,
    pulp_constants.LpStatusInfeasible: BackendStatus.INFEASIBLE,
    pulp_constants.LpStatusUnbounded: BackendStatus.UNBOUNDED,
    pulp_constants.LpStatusUndefined: BackendStatus.UNKNOWN,
}


# path to the cbc executable from the cbcbox package, None if it isnt installed
def _packaged_cbc_path() -> str | None:
    try:
        import cbcbox
    except ImportError:
        return None

    path: str | None = cbcbox.cbc_bin_path()
    return path


# creates a cbc solver, using the cbcbox executable if there is one
def _new_cbc_solver(options: BackendOptions) -> pulp.COIN_CMD:
    return pulp.COIN_CMD(
        msg=False,
        timeLimit=options.time_limit_seconds,
        path=_packaged_cbc_path(),
    )


# translates pulp's model and solution status into saw's BackendStatus
# unknown statuses stay UNKNOWN instead of claiming more than we know
def translate_pulp_status(
    status: int,
    solution_status: int | None = None,
) -> BackendStatus:
    # when cbc stops at a limit with a usable solution, pulp reports the model as
    # optimal but the solution as integer feasible, so it is only FEASIBLE
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


# creates, checks and runs the pulp/cbc solver
class PulpCbcAdapter:
    backend_id: Final[str] = "pulp_cbc"

    # reports whether pulp can find a cbc executable, never raises
    def check_availability(self) -> BackendAvailability:
        try:
            solver = _new_cbc_solver(BackendOptions())
            available = bool(solver.available())
        except Exception as exc:  # noqa: BLE001 - any failure means cbc is unavailable
            return BackendAvailability(
                available=False,
                reason=(f"PuLP/CBC availability check failed with {type(exc).__name__}: {exc}"),
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

    # creates the cbc solver with the given options
    def create_solver(self, options: BackendOptions) -> pulp.COIN_CMD:
        try:
            return _new_cbc_solver(options)
        except Exception as exc:
            raise PulpAdapterError("PuLP/CBC solver construction failed.") from exc

    # solves an already built pulp model and returns the translated status
    # cbc is checked again here since it could disappear after check_availability
    def solve(
        self,
        model: pulp.LpProblem,
        options: BackendOptions,
    ) -> BackendStatus:
        solver = self.create_solver(options)

        try:
            available = bool(solver.available())
        except Exception as exc:
            raise PulpAdapterError("PuLP/CBC availability check failed before solving.") from exc

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
