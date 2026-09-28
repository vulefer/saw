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
    findings.extend(_check_bounded_stock_consumption(problem, solution))
    findings.extend(_check_exact_demand_production(problem, solution))
    findings.extend(_check_objective_components(problem, solution))

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
                    message=(f"Pattern references unknown stock ID {pattern.stock_id!r}."),
                    stock_id=pattern.stock_id,
                    pattern_index=index,
                )
            )
        for demand_id in pattern.piece_counts:
            if isinstance(demand_id, str) and demand_id and demand_id not in known_demand_ids:
                findings.append(
                    VerificationFinding(
                        code=VerificationFindingCode.UNKNOWN_DEMAND_ID,
                        message=(f"Pattern references unknown demand ID {demand_id!r}."),
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
            isinstance(repetitions, int) and not isinstance(repetitions, bool) and repetitions > 0
        )

        if not is_positive_integer:
            findings.append(
                VerificationFinding(
                    code=VerificationFindingCode.NON_POSITIVE_REPETITIONS,
                    message=(
                        f"Pattern repetitions must be a positive integer; received {repetitions!r}."
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

                if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
                    structural_problems.append(
                        f"piece count for demand {demand_id!r} must be a "
                        f"positive integer; received {count!r}"
                    )

        if structural_problems:
            findings.append(
                VerificationFinding(
                    code=VerificationFindingCode.MALFORMED_PATTERN,
                    message=("Malformed pattern: " + "; ".join(structural_problems) + "."),
                    stock_id=(
                        cut_pattern.stock_id if isinstance(cut_pattern.stock_id, str) else None
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
                    message=(f"Pattern duplicates the canonical pattern at index {first_index}."),
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
    return sum(demand_by_id[demand_id].length * count for demand_id, count in piece_counts.items())


def _check_pattern_capacity(
    problem: Problem,
    solution: Solution,
) -> list[VerificationFinding]:
    findings: list[VerificationFinding] = []
    stocks_by_id = {stock.stock_id: stock for stock in problem.stocks}
    demand_by_id = {demand.demand_id: demand for demand in problem.demands}

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
    demands_by_id = {demand.demand_id: demand for demand in problem.demands}

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


def _check_bounded_stock_consumption(
    problem: Problem,
    solution: Solution,
) -> list[VerificationFinding]:
    findings: list[VerificationFinding] = []
    stocks_by_id = {stock.stock_id: stock for stock in problem.stocks}
    used_quantities: dict[str, int] = {}

    for cut_pattern in solution.cut_patterns:
        stock_id = cut_pattern.stock_id
        repetitions = cut_pattern.repetitions

        if not isinstance(stock_id, str) or stock_id not in stocks_by_id:
            continue
        if not isinstance(repetitions, int) or isinstance(repetitions, bool) or repetitions <= 0:
            continue

        used_quantities[stock_id] = used_quantities.get(stock_id, 0) + repetitions

    for stock in problem.stocks:
        used_quantity = used_quantities.get(stock.stock_id, 0)
        if used_quantity <= stock.quantity:
            continue

        findings.append(
            VerificationFinding(
                code=VerificationFindingCode.STOCK_QUANTITY_EXCEEDED,
                message=(
                    f"Patterns use stock {stock.stock_id!r} "
                    f"{used_quantity} times, exceeding available quantity "
                    f"{stock.quantity} by {used_quantity - stock.quantity}."
                ),
                stock_id=stock.stock_id,
            )
        )

    return findings


def _check_exact_demand_production(
    problem: Problem,
    solution: Solution,
) -> list[VerificationFinding]:
    findings: list[VerificationFinding] = []
    demand_by_id = {demand.demand_id: demand for demand in problem.demands}
    produced_quantities: dict[str, int] = {demand.demand_id: 0 for demand in problem.demands}

    for cut_pattern in solution.cut_patterns:
        repetitions = cut_pattern.repetitions
        if not isinstance(repetitions, int) or isinstance(repetitions, bool) or repetitions <= 0:
            continue

        for demand_id, piece_count in cut_pattern.piece_counts.items():
            if not isinstance(demand_id, str) or demand_id not in demand_by_id:
                continue
            if (
                not isinstance(piece_count, int)
                or isinstance(piece_count, bool)
                or piece_count <= 0
            ):
                continue

            produced_quantities[demand_id] += piece_count * repetitions

    for demand in problem.demands:
        produced_quantity = produced_quantities[demand.demand_id]
        if produced_quantity == demand.quantity:
            continue

        if produced_quantity < demand.quantity:
            code = VerificationFindingCode.DEMAND_UNDERPRODUCED
            difference = demand.quantity - produced_quantity
            comparison = f"underproduced by {difference}"
        else:
            code = VerificationFindingCode.DEMAND_OVERPRODUCED
            difference = produced_quantity - demand.quantity
            comparison = f"overproduced by {difference}"

        findings.append(
            VerificationFinding(
                code=code,
                message=(
                    f"Demand {demand.demand_id!r} requires quantity "
                    f"{demand.quantity}, but patterns produce "
                    f"{produced_quantity} ({comparison})."
                ),
                demand_id=demand.demand_id,
            )
        )

    return findings


def _has_positive_repetitions(cut_pattern: object) -> bool:
    repetitions = getattr(cut_pattern, "repetitions", None)
    return isinstance(repetitions, int) and not isinstance(repetitions, bool) and repetitions > 0


def _calculate_stock_count(solution: Solution) -> int:
    return sum(
        cut_pattern.repetitions
        for cut_pattern in solution.cut_patterns
        if _has_positive_repetitions(cut_pattern)
    )


def _calculate_leftover(problem: Problem, solution: Solution) -> int:
    stocks_by_id = {stock.stock_id: stock for stock in problem.stocks}
    demands_by_id = {demand.demand_id: demand for demand in problem.demands}
    total_leftover = 0

    for cut_pattern in solution.cut_patterns:
        if not _has_positive_repetitions(cut_pattern):
            continue

        if not isinstance(cut_pattern.stock_id, str):
            continue
        stock = stocks_by_id.get(cut_pattern.stock_id)
        if stock is None or any(
            not isinstance(demand_id, str)
            or demand_id not in demands_by_id
            or not isinstance(piece_count, int)
            or isinstance(piece_count, bool)
            for demand_id, piece_count in cut_pattern.piece_counts.items()
        ):
            # Reference and structure checks report patterns whose leftover
            # cannot be independently evaluated.
            continue

        used_length = _calculate_used_length(
            cut_pattern.piece_counts,
            demands_by_id,
        )
        total_leftover += (stock.length - used_length) * cut_pattern.repetitions

    return total_leftover


def calculate_distinct_pattern_count(solution: Solution) -> int:
    patterns: set[Pattern] = set()

    for cut_pattern in solution.cut_patterns:
        if not _has_positive_repetitions(cut_pattern):
            continue

        try:
            patterns.add(cut_pattern.pattern)
        except TypeError:
            # Pattern structure validation reports unhashable identities.
            continue

    return len(patterns)


def _check_objective_components(
    problem: Problem,
    solution: Solution,
) -> list[VerificationFinding]:
    findings: list[VerificationFinding] = []

    reported = solution.objective_value
    if reported is None:
        return findings

    recomputed_components = (
        ("stock_piece_count", _calculate_stock_count(solution)),
        ("total_leftover", _calculate_leftover(problem, solution)),
        (
            "distinct_pattern_count",
            calculate_distinct_pattern_count(solution),
        ),
    )

    for component_name, recomputed_value in recomputed_components:
        reported_value = getattr(reported, component_name)
        if (
            isinstance(reported_value, int)
            and not isinstance(reported_value, bool)
            and reported_value == recomputed_value
        ):
            continue

        findings.append(
            VerificationFinding(
                code=VerificationFindingCode.OBJECTIVE_MISMATCH,
                message=(
                    f"Reported objective {component_name!r} is "
                    f"{reported_value}, but the independently recomputed "
                    f"value is {recomputed_value}."
                ),
            )
        )

    return findings
