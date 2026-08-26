"""Complete-pattern exact backend.

The public names are re-exported here to preserve the original
``saw.backends.exact_enumerated`` import surface after the module was split by
responsibility.
"""

from saw.backends.exact_enumerated.backend import (
    solve_exact_enumerated_pipeline,
)
from saw.backends.exact_enumerated.model import (
    add_exact_demand_constraints,
    add_bounded_stock_constraints,
    build_pattern_use_indicators,
    build_pattern_variables,
    build_total_leftover_expression,
)
from saw.backends.exact_enumerated.output import (
    extract_backend_output,
    extract_positive_cut_patterns,
    translate_exact_backend_status,
)
from saw.backends.exact_enumerated.stages import (
    PrimaryStockCountResult,
    SecondaryLeftoverResult,
    TertiaryDistinctPatternCountResult,
    build_primary_stock_count_model,
    configure_secondary_leftover_stage,
    configure_tertiary_pattern_count_stage,
    solve_primary_stock_count_stage,
    solve_secondary_leftover_stage,
    solve_tertiary_pattern_count_stage,
)

__all__ = [
    "PrimaryStockCountResult",
    "SecondaryLeftoverResult",
    "TertiaryDistinctPatternCountResult",
    "add_exact_demand_constraints",
    "add_bounded_stock_constraints",
    "build_pattern_use_indicators",
    "build_pattern_variables",
    "build_primary_stock_count_model",
    "build_total_leftover_expression",
    "configure_secondary_leftover_stage",
    "configure_tertiary_pattern_count_stage",
    "extract_backend_output",
    "extract_positive_cut_patterns",
    "solve_exact_enumerated_pipeline",
    "solve_primary_stock_count_stage",
    "solve_secondary_leftover_stage",
    "solve_tertiary_pattern_count_stage",
    "translate_exact_backend_status",
]
