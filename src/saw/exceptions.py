'''exceptions raised by saw'''

# base class for exceptions raised by saw
class SawError(Exception):
    pass

# raised when input violates the data contract
class ValidationError(SawError):
    pass

# raised when the requested backend cannot be used
class BackendUnavailableError(SawError):
    pass

# raised when the backend result fails independent verification
class InternalVerificationError(SawError):
    pass