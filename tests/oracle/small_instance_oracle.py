"""A deliberately simple, test-only solver for tiny valid problems.

This module is independent of Saw's production pattern enumeration,
optimization formulation, and solution verification.  It expands bounded
inventory into individual stock pieces, expands demand into individual cut
pieces, and exhaustively tries every compatible placement.  That direct
search is intentionally exponential and must only be used for small tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from saw.models import Problem


@dataclass(frozen=True, order=True, slots=True)
class OracleObjective:
    """The independently computed lexicographic objective tuple."""

    stock_piece_count: int
    total_leftover: int
    distinct_pattern_count: int


@dataclass(frozen=True, slots=True)
class OracleResult:
    """Feasibility and, when feasible, the best objective found."""

    feasible: bool
    objective_value: OracleObjective | None


@dataclass(frozen=True, slots=True)
class _PhysicalStock:
    stock_id: str
    material_id: str
    length: int


@dataclass(frozen=True, slots=True)
class _DemandPiece:
    demand_id: str
    material_id: str
    length: int


def solve_small_instance(problem: Problem) -> OracleResult:
    """Exhaustively solve a small, valid Saw problem without production helpers."""

    stock_pieces = tuple(
        _PhysicalStock(stock.stock_id, stock.material_id, stock.length)
        for stock in problem.stocks
        for _ in range(stock.quantity)
    )
    demand_pieces = tuple(
        sorted(
            (
                _DemandPiece(demand.demand_id, demand.material_id, demand.length)
                for demand in problem.demands
                for _ in range(demand.quantity)
            ),
            key=lambda piece: (
                piece.material_id,
                -piece.length,
                piece.demand_id,
            ),
        )
    )

    remaining_lengths = [stock.length for stock in stock_pieces]
    piece_counts: list[dict[str, int]] = [{} for _ in stock_pieces]
    best: OracleObjective | None = None

    def visit(demand_index: int, used_stock_count: int) -> None:
        nonlocal best

        if best is not None and used_stock_count > best.stock_piece_count:
            return

        if demand_index == len(demand_pieces):
            used_indexes = tuple(index for index, counts in enumerate(piece_counts) if counts)
            patterns = {
                (
                    stock_pieces[index].stock_id,
                    tuple(sorted(piece_counts[index].items())),
                )
                for index in used_indexes
            }
            objective = OracleObjective(
                stock_piece_count=len(used_indexes),
                total_leftover=sum(remaining_lengths[index] for index in used_indexes),
                distinct_pattern_count=len(patterns),
            )

            if best is None or objective < best:
                best = objective
            return

        demand_piece = demand_pieces[demand_index]
        equivalent_stock_states: set[tuple[str, int, tuple[tuple[str, int], ...]]] = set()

        for stock_index, stock in enumerate(stock_pieces):
            if stock.material_id != demand_piece.material_id:
                continue
            if remaining_lengths[stock_index] < demand_piece.length:
                continue

            stock_state = (
                stock.stock_id,
                remaining_lengths[stock_index],
                tuple(sorted(piece_counts[stock_index].items())),
            )
            if stock_state in equivalent_stock_states:
                continue
            equivalent_stock_states.add(stock_state)

            was_unused = not piece_counts[stock_index]
            next_used_stock_count = used_stock_count + int(was_unused)
            if best is not None and next_used_stock_count > best.stock_piece_count:
                continue

            previous_count = piece_counts[stock_index].get(
                demand_piece.demand_id,
                0,
            )
            piece_counts[stock_index][demand_piece.demand_id] = previous_count + 1
            remaining_lengths[stock_index] -= demand_piece.length

            visit(demand_index + 1, next_used_stock_count)

            remaining_lengths[stock_index] += demand_piece.length
            if previous_count:
                piece_counts[stock_index][demand_piece.demand_id] = previous_count
            else:
                del piece_counts[stock_index][demand_piece.demand_id]

    visit(demand_index=0, used_stock_count=0)
    return OracleResult(feasible=best is not None, objective_value=best)
