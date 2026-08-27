from collections.abc import Mapping

from saw.models import (
    Demand,
    Problem,
    Solution,
    VerificationFinding,
    VerificationFindingCode,
    VerificationReport,
)
from saw.patterns import Pattern


def verify_solution(
    problem: Problem,
    solution: Solution,
) -> VerificationReport:
    findings: list[VerificationFinding] = []

    findings.extend(_check_solution_references(problem, solution))
    findings.extend(_check_repetition_counts(solution))
    findings.extend(_check_pattern_structure(solution))
    findings.extend(_check_duplicate_patterns(solution))
    findings.extend(_check_pattern_capacity(problem, solution))
    findings.extend(_check_material_compatibility(problem, solution))

    return VerificationReport(findings=tuple(findings))


def _check_solution_references(
    problem: Problem,
    solution: Solution,
) -> list[VerificationFinding]:
    """Check that every referenced stock and demand ID exists."""
    findings: list[VerificationFinding] = []

    known_stock_ids = {stock.stock_id for stock in problem.stocks}
    known_demand_ids = {demand.demand_id for demand in problem.demands}

    for index, pattern in enumerate(solution.cut_patterns):
        if (
            isinstance(pattern.stock_id, str)
            and pattern.stock_id
            and pattern.stock_id not in known_stock_ids
        ):
            findings.append(
                VerificationFinding(
                    code=VerificationFindingCode.UNKNOWN_STOCK_ID,
                    message=(
                        f"Pattern references unknown stock ID "
                        f"{pattern.stock_id!r}."
                    ),
                    stock_id=pattern.stock_id,
                    pattern_index=index,
                )
            )
        for demand_id in pattern.piece_counts:
            if (
                isinstance(demand_id, str)
                and demand_id
                and demand_id not in known_demand_ids
            ):
                findings.append(
                    VerificationFinding(
                        code=VerificationFindingCode.UNKNOWN_DEMAND_ID,
                        message=(
                            f"Pattern references unknown demand ID "
                            f"{demand_id!r}."
                        ),
                        demand_id=demand_id,
                        pattern_index=index,
                    )
                )
    return findings


def _check_repetition_counts(
    solution: Solution,
) -> list[VerificationFinding]:
    """Check that every repetition count is a positive integer."""
    findings: list[VerificationFinding] = []

    for pattern_index, cut_pattern in enumerate(solution.cut_patterns):
        repetitions = cut_pattern.repetitions

        is_positive_integer = (
            isinstance(repetitions, int)
            and not isinstance(repetitions, bool)
            and repetitions > 0
        )

        if not is_positive_integer:
            findings.append(
                VerificationFinding(
                    code=VerificationFindingCode.NON_POSITIVE_REPETITIONS,
                    message=(
                        "Pattern repetitions must be a positive integer; "
                        f"received {repetitions!r}."
                    ),
                    stock_id=cut_pattern.stock_id,
                    pattern_index=pattern_index,
                )
            )

    return findings


def _check_pattern_structure(
    solution: Solution,
) -> list[VerificationFinding]:
    """Check that patterns are non-empty and structurally valid."""
    findings: list[VerificationFinding] = []

    for pattern_index, cut_pattern in enumerate(solution.cut_patterns):
        structural_problems: list[str] = []

        if not isinstance(cut_pattern.stock_id, str) or not cut_pattern.stock_id:
            structural_problems.append("stock ID must be a non-empty string")

        if not cut_pattern.piece_counts:
            findings.append(
                VerificationFinding(
                    code=VerificationFindingCode.EMPTY_PATTERN,
                    message="Pattern must contain at least one piece.",
                    stock_id=cut_pattern.stock_id,
                    pattern_index=pattern_index,
                )
            )
        else:
            for demand_id, count in cut_pattern.piece_counts.items():
                if not isinstance(demand_id, str) or not demand_id:
                    structural_problems.append(
                        f"demand ID {demand_id!r} must be a non-empty string"
                    )

                if (
                    not isinstance(count, int)
                    or isinstance(count, bool)
                    or count <= 0
                ):
                    structural_problems.append(
                        f"piece count for demand {demand_id!r} must be a "
                        f"positive integer; received {count!r}"
                    )

        if structural_problems:
            findings.append(
                VerificationFinding(
                    code=VerificationFindingCode.MALFORMED_PATTERN,
                    message=(
                        "Malformed pattern: "
                        + "; ".join(structural_problems)
                        + "."
                    ),
                    stock_id=(
                        cut_pattern.stock_id
                        if isinstance(cut_pattern.stock_id, str)
                        else None
                    ),
                    pattern_index=pattern_index,
                )
            )

    return findings


def _check_duplicate_patterns(
    solution: Solution,
) -> list[VerificationFinding]:
    """Reject repeated canonical pattern identities."""
    findings: list[VerificationFinding] = []
    first_index_by_pattern: dict[Pattern, int] = {}

    for pattern_index, cut_pattern in enumerate(solution.cut_patterns):
        pattern = cut_pattern.pattern

        try:
            hash(pattern)
        except TypeError:
            # Pattern structure validation reports the malformed identity.
            continue

        first_index = first_index_by_pattern.get(pattern)

        if first_index is not None:
            findings.append(
                VerificationFinding(
                    code=VerificationFindingCode.DUPLICATE_PATTERN,
                    message=(
                        "Pattern duplicates the canonical pattern at index "
                        f"{first_index}."
                    ),
                    stock_id=cut_pattern.stock_id,
                    pattern_index=pattern_index,
                )
            )
        else:
            first_index_by_pattern[pattern] = pattern_index

    return findings

def _calculate_used_length(
    piece_counts: Mapping[str, int],
    demand_by_id: Mapping[str, Demand],
) -> int:
    return sum(
        demand_by_id[demand_id].length * count
        for demand_id, count in piece_counts.items()
    )


def _check_pattern_capacity(
    problem: Problem,
    solution: Solution,
) -> list[VerificationFinding]:
    findings: list[VerificationFinding] = []
    stocks_by_id = {stock.stock_id: stock for stock in problem.stocks}
    demand_by_id = {
        demand.demand_id: demand for demand in problem.demands
    }

    for pattern_index, cut_pattern in enumerate(solution.cut_patterns):
        if not isinstance(cut_pattern.stock_id, str):
            continue

        stock = stocks_by_id.get(cut_pattern.stock_id)
        if stock is None:
            continue

        if any(
            not isinstance(demand_id, str)
            or demand_id not in demand_by_id
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count <= 0
            for demand_id, count in cut_pattern.piece_counts.items()
        ):
            continue

        used_length = _calculate_used_length(
            cut_pattern.piece_counts,
            demand_by_id,
        )
        delta = stock.length - used_length

        if delta < 0:
            findings.append(
                VerificationFinding(
                    code=VerificationFindingCode.PATTERN_CAPACITY_EXCEEDED,
                    message=(
                        f"Pattern uses length {used_length}, exceeding "
                        f"stock length {stock.length} by {-delta}."
                    ),
                    stock_id=stock.stock_id,
                    pattern_index=pattern_index,
                )
            )

    return findings

def _check_material_compatibility(
    problem: Problem,
    solution: Solution,
) -> list[VerificationFinding]:
    findings: list[VerificationFinding] = []

    stocks_by_id = {stock.stock_id: stock for stock in problem.stocks}
    demands_by_id = {
        demand.demand_id: demand for demand in problem.demands
    }

    for pattern_index, cut_pattern in enumerate(solution.cut_patterns):
        if not isinstance(cut_pattern.stock_id, str):
            continue

        stock = stocks_by_id.get(cut_pattern.stock_id)
        if stock is None:
            continue

        for demand_id in cut_pattern.piece_counts:
            if not isinstance(demand_id, str):
                continue

            demand = demands_by_id.get(demand_id)
            if demand is None:
                continue

            if demand.material_id == stock.material_id:
                continue

            findings.append(
                VerificationFinding(
                    code=VerificationFindingCode.MATERIAL_MISMATCH,
                    message=(
                        f"Demand {demand.demand_id!r} uses material "
                        f"{demand.material_id!r}, which does not match "
                        f"stock {stock.stock_id!r} material "
                        f"{stock.material_id!r}."
                    ),
                    stock_id=stock.stock_id,
                    demand_id=demand.demand_id,
                    pattern_index=pattern_index,
                )
            )

    return findings
