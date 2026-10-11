from typing import Any

from pulp import constants as pulp_constants
import pytest

from saw.backends.base import BackendOptions
from saw.backends.pulp_backend import (
    PulpAdapterError,
    PulpCbcAdapter,
    translate_pulp_status,
)
from saw.exceptions import BackendUnavailableError
from saw.models import BackendStatus


class FakeSolver:
    def __init__(
        self,
        availability: object = True,
        availability_error: Exception | None = None,
    ) -> None:
        self.availability = availability
        self.availability_error = availability_error

    def available(self) -> object:
        if self.availability_error is not None:
            raise self.availability_error
        return self.availability


class FakeModel:
    def __init__(
        self,
        status: int = pulp_constants.LpStatusOptimal,
        solution_status: int = pulp_constants.LpSolutionOptimal,
        solve_error: Exception | None = None,
    ) -> None:
        self.status = status
        self.sol_status = solution_status
        self.solve_error = solve_error
        self.solver: object | None = None

    def solve(self, solver: object) -> int:
        self.solver = solver
        if self.solve_error is not None:
            raise self.solve_error
        return self.status


@pytest.mark.parametrize(
    ("pulp_status", "expected"),
    [
        (pulp_constants.LpStatusOptimal, BackendStatus.OPTIMAL),
        (pulp_constants.LpStatusNotSolved, BackendStatus.NOT_SOLVED),
        (pulp_constants.LpStatusInfeasible, BackendStatus.INFEASIBLE),
        (pulp_constants.LpStatusUnbounded, BackendStatus.UNBOUNDED),
        (pulp_constants.LpStatusUndefined, BackendStatus.UNKNOWN),
        (123_456, BackendStatus.UNKNOWN),
    ],
)
def test_pulp_statuses_are_translated_in_one_location(
    pulp_status: int,
    expected: BackendStatus,
) -> None:
    assert translate_pulp_status(pulp_status) is expected


@pytest.mark.parametrize(
    "pulp_status",
    [
        pulp_constants.LpStatusOptimal,
        pulp_constants.LpStatusNotSolved,
    ],
)
def test_integer_incumbent_without_proof_is_only_feasible(
    pulp_status: int,
) -> None:
    assert (
        translate_pulp_status(
            pulp_status,
            pulp_constants.LpSolutionIntegerFeasible,
        )
        is BackendStatus.FEASIBLE
    )


def test_unrecognized_model_status_remains_unknown_with_an_incumbent() -> None:
    assert (
        translate_pulp_status(
            123_456,
            pulp_constants.LpSolutionIntegerFeasible,
        )
        is BackendStatus.UNKNOWN
    )


def test_availability_reports_pulp_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from saw.backends import pulp_backend

    solver = FakeSolver(availability="C:/solver/cbc.exe")
    monkeypatch.setattr(
        pulp_backend.pulp,
        "COIN_CMD",
        lambda **kwargs: solver,
    )

    availability = PulpCbcAdapter().check_availability()

    assert availability.available is True
    assert availability.reason is None
    assert availability.backend_version == pulp_backend.pulp.__version__


def test_unavailable_cbc_has_an_actionable_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from saw.backends import pulp_backend

    monkeypatch.setattr(
        pulp_backend.pulp,
        "COIN_CMD",
        lambda **kwargs: FakeSolver(availability=None),
    )

    availability = PulpCbcAdapter().check_availability()

    assert availability.available is False
    assert availability.reason is not None
    assert "CBC" in availability.reason
    assert "PATH" in availability.reason


def test_availability_failure_retains_diagnostic_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from saw.backends import pulp_backend

    monkeypatch.setattr(
        pulp_backend.pulp,
        "COIN_CMD",
        lambda **kwargs: FakeSolver(availability_error=RuntimeError("solver lookup detail")),
    )

    availability = PulpCbcAdapter().check_availability()

    assert availability.available is False
    assert availability.reason is not None
    assert "RuntimeError" in availability.reason
    assert "solver lookup detail" in availability.reason


def test_solver_configuration_passes_the_time_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from saw.backends import pulp_backend

    captured: dict[str, Any] = {}
    solver = FakeSolver()

    def make_solver(**kwargs: Any) -> FakeSolver:
        captured.update(kwargs)
        return solver

    monkeypatch.setattr(pulp_backend.pulp, "COIN_CMD", make_solver)
    monkeypatch.setattr(
        pulp_backend,
        "_packaged_cbc_path",
        lambda: "C:/solver/cbc.exe",
    )

    created = PulpCbcAdapter().create_solver(BackendOptions(time_limit_seconds=12.5))

    assert created is solver
    assert captured == {
        "msg": False,
        "timeLimit": 12.5,
        "path": "C:/solver/cbc.exe",
    }


def test_solver_construction_failure_preserves_original_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from saw.backends import pulp_backend

    construction_error = RuntimeError("constructor detail")

    def fail_to_construct(**kwargs: Any) -> FakeSolver:
        raise construction_error

    monkeypatch.setattr(pulp_backend.pulp, "COIN_CMD", fail_to_construct)

    with pytest.raises(PulpAdapterError) as captured:
        PulpCbcAdapter().create_solver(BackendOptions())

    assert captured.value.__cause__ is construction_error


def test_solve_uses_the_configured_solver_and_translates_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from saw.backends import pulp_backend

    solver = FakeSolver()
    monkeypatch.setattr(
        pulp_backend.pulp,
        "COIN_CMD",
        lambda **kwargs: solver,
    )
    model = FakeModel()

    status = PulpCbcAdapter().solve(model, BackendOptions())

    assert status is BackendStatus.OPTIMAL
    assert model.solver is solver


def test_solve_rejects_an_unavailable_solver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from saw.backends import pulp_backend

    monkeypatch.setattr(
        pulp_backend.pulp,
        "COIN_CMD",
        lambda **kwargs: FakeSolver(availability=False),
    )

    with pytest.raises(BackendUnavailableError):
        PulpCbcAdapter().solve(FakeModel(), BackendOptions())


def test_unexpected_solve_failure_preserves_original_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from saw.backends import pulp_backend

    solver = FakeSolver()
    monkeypatch.setattr(
        pulp_backend.pulp,
        "COIN_CMD",
        lambda **kwargs: solver,
    )
    solve_error = RuntimeError("native solver detail")

    with pytest.raises(PulpAdapterError) as captured:
        PulpCbcAdapter().solve(
            FakeModel(solve_error=solve_error),
            BackendOptions(),
        )

    assert captured.value.__cause__ is solve_error
    assert "native solver detail" in str(captured.value.__cause__)
