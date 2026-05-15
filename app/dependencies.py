"""
Shared dependencies — cache and HTTP client lifecycle.

InMemoryCache is a simple TTL store keyed by string.
For production, swap the backend in _get / _set to use Redis:

    import redis.asyncio as redis
    r = redis.from_url(settings.redis_url)
    await r.set(key, json.dumps(value), ex=ttl_seconds)
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

CACHE_KEY_TENDERS          = "tenders:all"
CACHE_KEY_FAT_META         = "meta:find_a_tender"
CACHE_KEY_CF_META          = "meta:contracts_finder"
CACHE_KEY_S2W_META         = "s2w_meta"
CACHE_KEY_PCS_META         = "pcs_meta"
CACHE_KEY_LAST_REFRESHED   = "meta:last_refreshed"

@dataclass
class CacheEntry:
    value: Any
    expires_at: datetime


class InMemoryCache:
    """Thread-safe enough for single-process uvicorn. Use Redis for multi-worker."""

    def __init__(self):
        self._store: dict[str, CacheEntry] = {}

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None
        if datetime.now(timezone.utc) > entry.expires_at:
            del self._store[key]
            return None
        return entry.value

    def set(self, key: str, value: Any, ttl_minutes: int) -> None:
        self._store[key] = CacheEntry(
            value=value,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes),
        )

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()

    def is_populated(self) -> bool:
        return self.get(CACHE_KEY_TENDERS) is not None

    @property
    def size(self) -> int:
        # Purge expired entries on read
        now = datetime.now(timezone.utc)
        expired = [k for k, v in self._store.items() if now > v.expires_at]
        for k in expired:
            del self._store[k]
        return len(self._store)


# Singleton cache instance — shared across the app lifetime
cache = InMemoryCache()


# ── Source metadata helpers ───────────────────────────────────────────────────

@dataclass
class SourceMeta:
    last_fetched: Optional[datetime] = None
    tender_count: int = 0
    healthy: bool = True
    error: Optional[str] = None


def get_source_meta(key: str) -> SourceMeta:
    return cache.get(key) or SourceMeta()


def set_source_meta(key: str, meta: SourceMeta) -> None:
    cache._store[key] = CacheEntry(
        value=meta,
        expires_at=datetime.now(timezone.utc) + timedelta(days=365),
    )


def record_refresh_time() -> None:
    """Store the actual wall-clock time of the last successful refresh."""
    now = datetime.now(timezone.utc)
    cache._store[CACHE_KEY_LAST_REFRESHED] = CacheEntry(
        value=now,
        expires_at=now + timedelta(days=365),
    )


def get_last_refresh_time() -> Optional[datetime]:
    entry = cache._store.get(CACHE_KEY_LAST_REFRESHED)
    return entry.value if entry else None


def update_all_source_meta(source_counts: dict, errors: list) -> None:
    """Update SourceMeta for all four sources and record the refresh timestamp."""
    for key, label in [
        (CACHE_KEY_FAT_META, "Find a Tender"),
        (CACHE_KEY_CF_META,  "Contracts Finder"),
        (CACHE_KEY_S2W_META, "Sell2Wales"),
        (CACHE_KEY_PCS_META, "Public Contracts Scotland"),
    ]:
        set_source_meta(key, SourceMeta(
            last_fetched=datetime.now(timezone.utc),
            tender_count=source_counts.get(label, 0),
            healthy=not any(label in e for e in errors),
            error=next((e for e in errors if label in e), None),
        ))
    record_refresh_time()
