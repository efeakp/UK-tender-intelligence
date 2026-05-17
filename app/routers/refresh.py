"""
POST /refresh      — manually trigger a full data refresh (background).
POST /refresh/sync — synchronous refresh (blocking, rate-limited).
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from fastapi import APIRouter, BackgroundTasks, HTTPException
from app.config import settings
from app.dependencies import (
    cache,
    CACHE_KEY_TENDERS,
    update_all_source_meta,
    get_prev_tenders,
    set_prev_tenders,
)
from app.models.tender import RefreshResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/refresh", tags=["Refresh"])

_refresh_lock = asyncio.Lock()

# Minimum interval between /refresh/sync calls (30 minutes) to prevent
# quota depletion when startup warm-up and manual refresh overlap.
MIN_REFRESH_INTERVAL_S: int = 1800
_last_refresh_completed: datetime | None = None


@router.post("", response_model=RefreshResponse, summary="Trigger manual refresh")
async def trigger_refresh(background_tasks: BackgroundTasks):
    """
    Kicks off a background refresh. Returns immediately.
    Poll GET /sources to see when the refresh completes.
    """
    if _refresh_lock.locked():
        return RefreshResponse(
            success=False,
            message="A refresh is already in progress. Check GET /sources for status.",
            tenders_fetched=0,
            duration_seconds=0,
        )
    background_tasks.add_task(_do_refresh)
    return RefreshResponse(
        success=True,
        message="Refresh started in background. Poll GET /sources for completion.",
        tenders_fetched=0,
        duration_seconds=0,
    )


@router.post("/sync", response_model=RefreshResponse, summary="Synchronous refresh (blocking)")
async def trigger_refresh_sync():
    """
    Runs the refresh synchronously and returns only when complete.
    Rejects requests made within 30 minutes of the last completed refresh.
    """
    global _last_refresh_completed
    if _last_refresh_completed is not None:
        elapsed_s = (datetime.now(timezone.utc) - _last_refresh_completed).total_seconds()
        remaining = int(MIN_REFRESH_INTERVAL_S - elapsed_s)
        if remaining > 0:
            logger.warning(
                "Manual /refresh/sync rejected — last refresh completed %ds ago "
                "(minimum interval %ds, %dm%ds remaining)",
                int(elapsed_s), MIN_REFRESH_INTERVAL_S, remaining // 60, remaining % 60,
            )
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Refresh rate limited — last refresh completed {int(elapsed_s)}s ago. "
                    f"Next manual refresh allowed in {remaining // 60}m {remaining % 60}s. "
                    f"The scheduled refresh runs automatically at 10:15 UTC daily."
                ),
            )
    return await _do_refresh()


async def _do_refresh() -> RefreshResponse:
    async with _refresh_lock:
        global _last_refresh_completed
        start = time.monotonic()
        try:
            from app.services.aggregator import refresh_all

            previous_tenders = get_prev_tenders()
            tenders, errors, raw_source_counts = await refresh_all(
                days_back=settings.refresh_days_back,
                previous_tenders=previous_tenders,
            )

            cache.set(CACHE_KEY_TENDERS, tenders, ttl_minutes=settings.cache_ttl_minutes)
            set_prev_tenders(tenders)

            update_all_source_meta(raw_source_counts, errors)

            elapsed = time.monotonic() - start
            _last_refresh_completed = datetime.now(timezone.utc)
            return RefreshResponse(
                success=True,
                message=f"Refresh complete. {len(tenders)} tenders cached.",
                tenders_fetched=len(tenders),
                duration_seconds=round(elapsed, 2),
                errors=errors,
            )

        except Exception as e:
            elapsed = time.monotonic() - start
            logger.error("Manual refresh failed: %s", e, exc_info=True)
            return RefreshResponse(
                success=False,
                message=f"Refresh failed: {e}",
                tenders_fetched=0,
                duration_seconds=round(elapsed, 2),
                errors=[str(e)],
            )
