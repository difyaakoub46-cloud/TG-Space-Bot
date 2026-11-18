"""A tiny TTL cache for the read-only queries a screen needs.

Every button press used to rebuild its screen from scratch: the library totals,
the folders of the user and the storage-group check were asked again on every tap
even though they only change when the library changes. Keeping the answer for a
few seconds makes a second tap inside that window instant, and every write path
clears the cache so a new upload is visible immediately.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

T = TypeVar("T")


@dataclass(slots=True)
class _Entry:
    value: Any
    expires_at: float


class TTLCache:
    """Keyed cache with a fixed lifetime and single-flight loading.

    Two taps that arrive together ask the database once: the second one waits for
    the first loader instead of starting a duplicate request.
    """

    def __init__(
        self, ttl: float, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._ttl = max(0.0, float(ttl))
        self._clock = clock
        self._entries: dict[str, _Entry] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _fresh(self, key: str) -> _Entry | None:
        entry = self._entries.get(key)
        if entry is not None and entry.expires_at > self._clock():
            return entry
        return None

    async def get(self, key: str, factory: Callable[[], Awaitable[T]]) -> T:
        """The cached value, or the result of ``factory`` stored for the TTL."""
        entry = self._fresh(key)
        if entry is not None:
            return entry.value

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            entry = self._fresh(key)
            if entry is not None:
                return entry.value
            value = await factory()
            if self._ttl > 0:
                self._entries[key] = _Entry(value, self._clock() + self._ttl)
            return value

    def invalidate(self, *keys: str) -> None:
        for key in keys:
            self._entries.pop(key, None)

    def invalidate_prefix(self, prefix: str) -> None:
        for key in [name for name in self._entries if name.startswith(prefix)]:
            self._entries.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()


__all__ = ["TTLCache"]
