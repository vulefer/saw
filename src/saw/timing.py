"""A single monotonic deadline shared across every phase of one ``solve()`` call.

See docs/resource-limits.md ("One end-to-end time budget"): ``time_limit_seconds``
is one deadline for the whole call, not a fresh allowance re-issued to each
phase. :class:`Deadline` is the shared object that lets pattern enumeration
and all three lexicographic objective stages agree on how much time is left,
instead of each phase measuring out the originally configured value again
from its own start.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter


@dataclass(frozen=True, slots=True)
class Deadline:
    """One absolute point in monotonic time, or no deadline at all.

    ``deadline_at`` is expressed in the same clock as ``time.perf_counter()``.
    ``None`` means the caller configured ``time_limit_seconds=None``: every
    query below reports an unlimited remainder and the deadline never
    expires.
    """

    deadline_at: float | None

    @classmethod
    def from_start(
        cls,
        started_at: float,
        time_limit_seconds: float | None,
    ) -> Deadline:
        """Anchor a deadline to ``started_at``, a prior ``perf_counter()`` read.

        Per docs/resource-limits.md, the end-to-end budget starts on entry
        to ``solve()``, before problem validation. Callers should pass the
        ``perf_counter()`` value captured at that entry point rather than
        the current time, so that time already spent before this call is
        counted against the deadline rather than granted back to it.
        """

        if time_limit_seconds is None:
            return cls(None)
        return cls(started_at + time_limit_seconds)

    def remaining_seconds(self) -> float | None:
        """Seconds left before the deadline, or ``None`` when unlimited.

        Callers are expected to call this again immediately before every
        expensive phase or solver call (see docs/resource-limits.md) rather
        than caching one value, since each call reflects the current
        monotonic time and can therefore return a smaller number than the
        last call, or a non-positive one.
        """

        if self.deadline_at is None:
            return None
        return self.deadline_at - perf_counter()

    def expired(self) -> bool:
        """Whether the deadline has already passed.

        A remainder of exactly zero counts as expired: docs/resource-limits.md
        requires "If the remainder is not positive, Saw does not start that
        stage."
        """

        remaining = self.remaining_seconds()
        return remaining is not None and remaining <= 0
