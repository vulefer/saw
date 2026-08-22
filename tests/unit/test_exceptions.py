import pytest

from saw import (
    BackendUnavailableError,
    InternalVerificationError,
    SawError,
    ValidationError,
    ValidationErrorCode,
)
from saw import SolutionStatus


@pytest.mark.parametrize(
    "exception_type",
    [
        ValidationError,
        BackendUnavailableError,
        InternalVerificationError,
    ],
)
def test_public_exceptions_inherit_from_saw_error(
    exception_type: type[SawError],
) -> None:
    assert issubclass(exception_type, SawError)
    assert issubclass(exception_type, Exception)


@pytest.mark.parametrize(
    ("exception_type", "message"),
    [
        (ValidationError, "Stock length must be positive."),
        (BackendUnavailableError, "The requested backend is unavailable."),
        (
            InternalVerificationError,
            "The backend result failed independent verification.",
        ),
    ],
)
def test_public_exceptions_preserve_their_message(
    exception_type: type[SawError],
    message: str,
) -> None:
    error = exception_type(message)

    assert str(error) == message


def test_specific_exception_can_be_caught_as_saw_error() -> None:
    with pytest.raises(SawError) as captured:
        raise ValidationError("Invalid problem input.")

    assert type(captured.value) is ValidationError


def test_validation_error_defaults_to_a_stable_general_code() -> None:
    error = ValidationError("Invalid problem input.")

    assert error.code is ValidationErrorCode.INVALID_PROBLEM
    assert error.record is None
    assert error.record_index is None
    assert error.field is None
    assert error.conflicting_ids == ()


def test_backend_error_preserves_chained_internal_cause() -> None:
    backend_error = RuntimeError("backend-specific detail")

    with pytest.raises(BackendUnavailableError) as captured:
        try:
            raise backend_error
        except RuntimeError as exc:
            raise BackendUnavailableError(
                "The requested backend is unavailable."
            ) from exc

    assert captured.value.__cause__ is backend_error
    assert str(captured.value) == "The requested backend is unavailable."


def test_proven_infeasibility_is_a_solution_status_not_an_exception() -> None:
    assert SolutionStatus.INFEASIBLE.value == "infeasible"
    assert not issubclass(SolutionStatus, BaseException)
