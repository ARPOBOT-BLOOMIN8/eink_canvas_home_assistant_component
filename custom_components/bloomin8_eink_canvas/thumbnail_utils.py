"""Shared helpers for thumbnail proxying/caching.

This module intentionally contains *no* Home Assistant entry points.
It is used by both:
- `media_player.py` (browse-image proxy via MediaPlayerEntity)
- `media_source.py` (custom HTTP view for media source thumbnails)
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import time
from typing import Generic, TypeVar


def guess_image_content_type(path: str) -> str:
    """Best-effort content type guess from filename extension."""
    lower = (path or "").lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".gif"):
        return "image/gif"
    if lower.endswith(".bmp"):
        return "image/bmp"
    if lower.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"


def safe_path_segment(value: str) -> str:
    """Validate a single path segment (no slashes, no traversal)."""
    v = (value or "").strip()
    if not v or "/" in v or "\\" in v or ".." in v:
        raise ValueError("Invalid path segment")
    return v


@dataclass(frozen=True, slots=True)
class BytesCacheEntry:
    fetched_at: float
    data: bytes
    content_type: str


TKey = TypeVar("TKey")


class BytesLruTtlCache(Generic[TKey]):
    """Tiny in-memory LRU cache with TTL for (bytes, content_type) values.

    This class is *not* thread-safe; callers are expected to guard it with a lock.
    """

    def __init__(self, *, ttl_seconds: int, max_items: int) -> None:
        self._ttl_seconds = int(ttl_seconds)
        self._max_items = int(max_items)
        self._data: OrderedDict[TKey, BytesCacheEntry] = OrderedDict()

    def get_any(self, key: TKey) -> BytesCacheEntry | None:
        """Return cached entry even if stale (does not check TTL)."""
        entry = self._data.get(key)
        if entry is None:
            return None
        # LRU bump
        self._data.move_to_end(key)
        return entry

    def get_fresh(self, key: TKey, *, now: float | None = None) -> BytesCacheEntry | None:
        """Return cached entry only if within TTL."""
        entry = self._data.get(key)
        if entry is None:
            return None
        now_v = time.monotonic() if now is None else float(now)
        if (now_v - entry.fetched_at) >= self._ttl_seconds:
            return None
        # LRU bump
        self._data.move_to_end(key)
        return entry

    def set(self, key: TKey, *, data: bytes, content_type: str, now: float | None = None) -> None:
        now_v = time.monotonic() if now is None else float(now)
        self._data[key] = BytesCacheEntry(now_v, data, content_type)
        self._data.move_to_end(key)
        while len(self._data) > self._max_items:
            self._data.popitem(last=False)
