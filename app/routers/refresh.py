"""
POST /refresh — manually trigger a full data refresh.
Useful for:
- First-time setup (cache is empty on cold start)
- Forcing an immediate update outside the scheduled window
- After changing keyword config
"""
import logging
import time
from datetime import datetime, timezone
from fastapi import APIRouter, BackgroundTasks, HTTPException
from app.config import settings
from app.dependencies import (
    cache,
    CACHE_KEY_TENDERS,
    CACHE_KEY_FAT_META,
    CACHE_KEY_CF_META,
    CACHE_KEY_S2W_META,
    CACHE_KEY_PCS_META,
    SourceMeta,
    set_source_meta,
)
from app.models.tender import RefreshResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/refresh", tags=["Refresh"])

# Simple lock to prevent concurrent refreshes
_refresh_in_progress = False

# Minimum interval between manual /refresh/sync calls (30 minutes)
# Prevents quota depletion when startup warm-up and manual refresh overlap
MIN_REFRESH_INTERVAL_S: int = 1800
_last_refresh_completed: datetime | None = None


@router.post("", response_model=RefreshResponse, summary="Trigger manual refresh")
async def trigger_refresh(background_tasks: BackgroundTasks):
    """
    Kicks off a background refresh. Returns immediately with a status message.
    Poll GET /sources to see when the refresh completes.
    """
    global _refresh_in_progress
    if _refresh_in_progress:
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
    Rejects requests made within 30 minutes of the last completed refresh
    to prevent quota depletion from back-to-back full fetches.
    Useful for scripts and CI pipelines.
    """
    global _last_refresh_completed
    # ── Minimum interval guard ────────────────────────────────────────────────
    if _last_refresh_completed is not None:
        elapsed_s = (datetime.now(timezone.utc) - _last_refresh_completed).total_seconds()
        remaining  = int(MIN_REFRESH_INTERVAL_S - elapsed_s)
        if remaining > 0:
            import logging
            logging.getLogger(__name__).warning(
                "Manual /refresh/sync rejected — last refresh completed %ds ago "
                "(minimum interval %ds, %dm%ds remaining)",
                int(elapsed_s),
                MIN_REFRESH_INTERVAL_S,
                remaining // 60,
                remaining % 60,
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
    global _refresh_in_progress
    _refresh_in_progress = True
    start = time.monotonic()
    try:
        from app.services.aggregator import refresh_all

        # Pass current cache to aggregator so it can preserve per-source
        # results if a source is rate-limited (429) or temporarily unavailable
        previous_tenders = cache.get(CACHE_KEY_TENDERS) or []

        tenders, errors = await refresh_all(
            days_back=settings.refresh_days_back,
            previous_tenders=previous_tenders,
        )

        cache.set(CACHE_KEY_TENDERS, tenders, ttl_minutes=settings.cache_ttl_minutes)

        now = datetime.now(timezone.utc)
        fat_count = sum(1 for t in tenders if t.source == "Find a Tender")
        cf_count  = sum(1 for t in tenders if t.source == "Contracts Finder")
        s2w_count = sum(1 for t in tenders if t.source == "Sell2Wales")
        pcs_count = sum(1 for t in tenders if t.source == "Public Contracts Scotland")

        set_source_meta(
            CACHE_KEY_FAT_META,
            SourceMeta(
                last_fetched=now,
                tender_count=fat_count,
                healthy=not any("Find a Tender" in e for e in errors),
                error=next((e for e in errors if "Find a Tender" in e), None),
            ),
        )
        set_source_meta(
            CACHE_KEY_CF_META,
            SourceMeta(
                last_fetched=now,
                tender_count=cf_count,
                healthy=not any("Contracts Finder" in e for e in errors),
                error=next((e for e in errors if "Contracts Finder" in e), None),
            ),
        )
        set_source_meta(
            CACHE_KEY_S2W_META,
            SourceMeta(
                last_fetched=now,
                tender_count=s2w_count,
                healthy=not any("Sell2Wales" in e for e in errors),
                error=next((e for e in errors if "Sell2Wales" in e), None),
            ),
        )
        set_source_meta(
            CACHE_KEY_PCS_META,
            SourceMeta(
                last_fetched=now,
                tender_count=pcs_count,
                healthy=not any("Public Contracts Scotland" in e for e in errors),
                error=next((e for e in errors if "Public Contracts Scotland" in e), None),
            ),
        )

        elapsed = time.monotonic() - start
        global _last_refresh_completed
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
    finally:
        _refresh_in_progress = False