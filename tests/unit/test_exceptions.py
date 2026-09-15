import pytest

from saw import (
    BackendUnavailableError,
    InternalVerificationError,
    SawError,
    SerializationError,
    SerializationErrorCode,
    ValidationError,
    ValidationErrorCode,
)
from saw import SolutionStatus


@pytest.mark.parametrize(
    "exception_type",
    [
        ValidationError,
        SerializationError,
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


def test_serialization_error_preserves_stable_code_and_context() -> None:
    error = SerializationError(
        "Stock length must be a positive integer.",
        code=SerializationErrorCode.SCHEMA_VIOLATION,
        record="stock",
        record_index=2,
        field="length",
    )

    assert str(error) == "Stock length must be a positive integer."
    assert error.code is SerializationErrorCode.SCHEMA_VIOLATION
    assert error.record == "stock"
    assert error.record_index == 2
    assert error.field == "length"


def test_serialization_error_preserves_its_human_readable_message() -> None:
    message = "Input is not valid JSON."
    error = SerializationError(
        message,
        code=SerializationErrorCode.INVALID_JSON,
    )

    assert str(error) == message


def test_serialization_error_context_is_optional() -> None:
    error = SerializationError(
        "Input is not valid JSON.",
        code=SerializationErrorCode.INVALID_JSON,
    )

    assert error.record is None
    assert error.record_index is None
    assert error.field is None


def test_serialization_error_requires_an_explicit_category() -> None:
    with pytest.raises(TypeError, match="code"):
        SerializationError("Serialized input is invalid.")  # type: ignore[call-arg]


def test_serialization_error_categories_have_stable_values() -> None:
    assert {
        code.name: code.value
        for code in SerializationErrorCode
    } == {
        "INVALID_JSON": "invalid_json",
        "UNSUPPORTED_FORMAT_VERSION": "unsupported_format_version",
        "SCHEMA_VIOLATION": "schema_violation",
        "SEMANTIC_VALIDATION": "semantic_validation",
    }


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
