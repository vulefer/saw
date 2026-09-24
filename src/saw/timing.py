from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter


@dataclass(frozen=True, slots=True)
class Deadline:
    deadline_at: float | None

    # this is just a factory method, returns a Deadline instance with the
    # given time limit
    @classmethod
    def from_start(
        cls,
        started_at: float,
        time_limit_seconds: float | None,
    ) -> Deadline:
        if time_limit_seconds is None:
            return cls(None)
        return cls(started_at + time_limit_seconds)

    def remaining_seconds(self) -> float | None:
        if self.deadline_at is None:
            return None
        return self.deadline_at - perf_counter()

    def expired(self) -> bool:
        remaining = self.remaining_seconds()
        return remaining is not None and remaining <= 0
