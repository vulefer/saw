'''exceptions raised by saw'''

from enum import Enum


# base class for exceptions raised by saw
class SawError(Exception):
    pass


class ValidationErrorCode(str, Enum):

    INVALID_PROBLEM = "invalid_problem"
    INVALID_STOCK_ID = "invalid_stock_id"
    INVALID_STOCK_MATERIAL_ID = "invalid_stock_material_id"
    INVALID_STOCK_LENGTH = "invalid_stock_length"
    INVALID_STOCK_QUANTITY = "invalid_stock_quantity"
    INVALID_DEMAND_ID = "invalid_demand_id"
    INVALID_DEMAND_MATERIAL_ID = "invalid_demand_material_id"
    INVALID_DEMAND_LENGTH = "invalid_demand_length"
    INVALID_DEMAND_QUANTITY = "invalid_demand_quantity"
    DUPLICATE_STOCK_ID = "duplicate_stock_id"
    DUPLICATE_DEMAND_ID = "duplicate_demand_id"


# raised when input violates the data contract
class ValidationError(SawError):
    def __init__(
        self,
        message: str,
        *,
        code: ValidationErrorCode = ValidationErrorCode.INVALID_PROBLEM,
        record: str | None = None,
        record_index: int | None = None,
        field: str | None = None,
        conflicting_ids: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.record = record
        self.record_index = record_index
        self.field = field
        self.conflicting_ids = tuple(conflicting_ids)


class SerializationErrorCode(str, Enum):
    """Stable categories for failures while reading versioned data."""

    INVALID_JSON = "invalid_json"
    UNSUPPORTED_FORMAT_VERSION = "unsupported_format_version"
    SCHEMA_VIOLATION = "schema_violation"
    SEMANTIC_VALIDATION = "semantic_validation"


class SerializationError(SawError):
    """Raised when versioned serialized data cannot be read safely.

    ``message`` is intended for people and is not a stable interface. Callers
    should branch on ``code`` and may use the optional context to identify a
    record or field without depending on a JSON-schema library's path format.
    """

    def __init__(
        self,
        message: str,
        *,
        code: SerializationErrorCode,
        record: str | None = None,
        record_index: int | None = None,
        field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.record = record
        self.record_index = record_index
        self.field = field

    @property
    def error_code(self) -> "ErrorCode":
        """The version 1 error-envelope code for this failure."""
        return _SERIALIZATION_ERROR_CODES[self.code]


class ErrorCode(str, Enum):
    """The closed set of version 1 error-envelope codes (docs/formats.md)."""

    INVALID_CLI_USAGE = "invalid_cli_usage"
    INVALID_JSON = "invalid_json"
    UNSUPPORTED_FORMAT_VERSION = "unsupported_format_version"
    INVALID_PROBLEM = "invalid_problem"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    INTERNAL_ERROR = "internal_error"
    INTERNAL_VERIFICATION_ERROR = "internal_verification_error"
    INPUT_READ_ERROR = "input_read_error"
    OUTPUT_WRITE_ERROR = "output_write_error"


# A problem that fails its schema or its semantic validation is reported the
# same way: the document was readable but is not a valid problem.
_SERIALIZATION_ERROR_CODES: dict[SerializationErrorCode, ErrorCode] = {
    SerializationErrorCode.INVALID_JSON: ErrorCode.INVALID_JSON,
    SerializationErrorCode.UNSUPPORTED_FORMAT_VERSION: ErrorCode.UNSUPPORTED_FORMAT_VERSION,
    SerializationErrorCode.SCHEMA_VIOLATION: ErrorCode.INVALID_PROBLEM,
    SerializationErrorCode.SEMANTIC_VALIDATION: ErrorCode.INVALID_PROBLEM,
}


# raised when the requested backend cannot be used
class BackendUnavailableError(SawError):
    pass


# raised when the backend result fails independent verification
class InternalVerificationError(SawError):
    pass
