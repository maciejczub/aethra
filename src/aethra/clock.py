"""Time sources.

Everything that needs precise timing uses :func:`now` (``time.perf_counter``):
it is monotonic and high resolution on every platform (QueryPerformanceCounter
on Windows). ``time.monotonic`` - and therefore ``loop.time()`` in asyncio - is
only ~15.6 ms resolution on Windows before Python 3.13, so timed waits go
through :func:`sleep_until`, which re-checks against ``perf_counter``.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime

now = time.perf_counter


class WallClock:
    """Maps :func:`now` timestamps to wall-clock time (for logs only)."""

    def __init__(self) -> None:
        self.mono0 = now()
        self.wall0 = time.time()

    def wall(self, t: float) -> float:
        return self.wall0 + (t - self.mono0)

    def iso(self, t: float) -> str:
        return datetime.fromtimestamp(self.wall(t)).astimezone().isoformat(timespec="microseconds")

    def hms(self, t: float) -> str:
        return datetime.fromtimestamp(self.wall(t)).strftime("%H:%M:%S.%f")[:-3]


async def sleep_until(target: float) -> None:
    """Sleep until ``now() >= target`` with ~1 ms accuracy.

    asyncio may wake up early (coarse monotonic clock) or late (timer tick);
    re-checking against perf_counter fixes the former. On Windows the timer
    resolution is raised to 1 ms at startup (see :mod:`aethra.platform`).
    """
    while True:
        remaining = target - now()
        if remaining <= 0:
            return
        # Sleep slightly short of the target, then spin on short sleeps.
        await asyncio.sleep(remaining - 0.002 if remaining > 0.004 else 0.0005)
