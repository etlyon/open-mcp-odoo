"""A per-connection sliding window for upstream requests.

One process only: a second worker does not share the counts. Each app keeps its own
``Throttle`` instance and keys it by the calling connection.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable


class Throttle:
    """``limit`` upstream requests per ``window`` seconds, per key (a connection)."""

    def __init__(self, limit: int = 60, window: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self.limit = limit
        self.window = window
        self.clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def take(self, key: str) -> float:
        """Count one request for ``key`` and return 0, or the seconds to wait (nothing counted)."""
        now = self.clock()
        with self._lock:
            for other in [name for name, hits in self._hits.items() if not hits or now - hits[-1] > self.window]:
                del self._hits[other]
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return self.window - (now - hits[0])
            hits.append(now)
            return 0.0
