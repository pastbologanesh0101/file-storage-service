"""Injectable clock so tests can control "now" without real sleeping."""
import time


class Clock:
    """Default clock backed by the real wall clock."""

    def now(self) -> float:
        return time.time()


class FixedClock(Clock):
    """A mockable clock for tests: set/advance the time explicitly."""

    def __init__(self, start: float = 0.0):
        self._now = start

    def now(self) -> float:
        return self._now

    def set(self, value: float) -> None:
        self._now = value

    def advance(self, seconds: float) -> None:
        self._now += seconds
