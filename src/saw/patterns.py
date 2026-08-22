from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class Pattern:
    stock_id: str
    # the field options make sure piece_counts isnt used in hashing
    piece_counts: Mapping[str, int] = field(compare=False, hash=False)
    # init=False meand this cannot be passed into the constructor
    # repr=False means the field is hidden even when printing the instance
    _normalized_counts: tuple[tuple[str, int], ...] = field(
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        for demand_id, count in self.piece_counts.items():
            if count < 0:
                raise ValueError(f"Negative piece count for demand {demand_id!r}")

        normalized_counts = tuple(sorted(
            (demand_id, count)
            for demand_id, count in self.piece_counts.items()
            if count > 0
        ))
        object.__setattr__(self, "_normalized_counts", normalized_counts)
        object.__setattr__(
            self,
            "piece_counts",
            MappingProxyType(dict(normalized_counts)),
        )
