import saw


EXPECTED_PUBLIC_API = {
    "BackendStatus",
    "BackendUnavailableError",
    "CutPattern",
    "Demand",
    "Diagnostics",
    "InternalVerificationError",
    "ObjectiveStage",
    "ObjectiveValue",
    "Problem",
    "SawError",
    "SerializationError",
    "SerializationErrorCode",
    "Solution",
    "SolutionStatus",
    "SolveOptions",
    "Stock",
    "TerminationReason",
    "ValidationError",
    "ValidationErrorCode",
    "VerificationFinding",
    "VerificationFindingCode",
    "VerificationReport",
    "VerificationResult",
    "solve",
    "validate_problem",
    "verify_solution",
}


def test_public_api_surface_is_exact() -> None:
    declared_api = tuple(saw.__all__)

    assert len(declared_api) == len(set(declared_api))
    assert set(declared_api) == EXPECTED_PUBLIC_API
    assert all(hasattr(saw, name) for name in declared_api)


def test_backend_implementations_are_not_public() -> None:
    assert "OptimizationBackend" not in saw.__all__
    assert "ExactEnumeratedBackend" not in saw.__all__
    assert not hasattr(saw, "OptimizationBackend")
    assert not hasattr(saw, "ExactEnumeratedBackend")
