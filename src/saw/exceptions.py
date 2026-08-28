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

# raised when the requested backend cannot be used
class BackendUnavailableError(SawError):
    pass

# raised when the backend result fails independent verification
class InternalVerificationError(SawError):
    pass
