"""How many times a text has been offered to the number validator (G-04): the first failure is returned to the agent, the
second one ends in the template. Kept in memory: an attempt belongs to one agent run, which is short."""

from collections import Counter
from collections.abc import Hashable
from threading import Lock


class ValidationAttempts:
    def __init__(self) -> None:
        self._counts: Counter[Hashable] = Counter()
        self._lock = Lock()

    def next(self, key: Hashable) -> int:
        """The number of this attempt for `key` (1 for the first)."""
        with self._lock:
            self._counts[key] += 1
            return self._counts[key]

    def clear(self, key: Hashable) -> None:
        """The text under `key` is settled (accepted or replaced by the template); the next one starts at attempt 1."""
        with self._lock:
            self._counts.pop(key, None)
