"""
Background scheduler — runs a daily tender refresh using APScheduler.
Default schedule: 10:15 UTC daily (configurable via REFRESH_CRON in .env).
Fetch window: configurable via REFRESH_DAYS_BACK in .env (default: 30 days).
"""
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

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

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="UTC")


async def _run_digest():
    """Send daily tender digest to Teams and email at 08:00 UTC."""
    from app.routers.digest import run_scheduled_digest
    logger.info("Running scheduled digest at %s", datetime.now(timezone.utc).isoformat())
    try:
        await run_scheduled_digest()
    except Exception as e:
        logger.error("Scheduled digest failed: %s", e, exc_info=True)


async def _run_refresh():
    """Inner async refresh — imported lazily to avoid circular imports."""
    from app.services.aggregator import refresh_all

    logger.info("Scheduled refresh starting at %s", datetime.now(timezone.utc).isoformat())
    try:
        previous_tenders = cache.get(CACHE_KEY_TENDERS) or []

        tenders, errors = await refresh_all(
            days_back=settings.refresh_days_back,
            previous_tenders=previous_tenders,
        )

        cache.set(CACHE_KEY_TENDERS, tenders, ttl_minutes=settings.cache_ttl_minutes)

        fat_count = sum(1 for t in tenders if t.source == "Find a Tender")
        cf_count  = sum(1 for t in tenders if t.source == "Contracts Finder")
        s2w_count = sum(1 for t in tenders if t.source == "Sell2Wales")
        pcs_count = sum(1 for t in tenders if t.source == "Public Contracts Scotland")
        now = datetime.now(timezone.utc)

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

        logger.info(
            "Scheduled refresh complete: %d tenders cached (window: %d days) — FaT:%d CF:%d S2W:%d PCS:%d",
            len(tenders),
            settings.refresh_days_back,
            fat_count,
            cf_count,
            s2w_count,
            pcs_count,
        )

    except Exception as e:
        logger.error("Scheduled refresh failed: %s", e, exc_info=True)


def start_scheduler():
    """Parse REFRESH_CRON and start the scheduler."""
    cron_parts = settings.refresh_cron.split()
    if len(cron_parts) != 5:
        logger.warning(
            "Invalid REFRESH_CRON '%s', falling back to 10:15 UTC daily",
            settings.refresh_cron,
        )
        trigger = CronTrigger(hour=10, minute=15, timezone="UTC")
    else:
        minute, hour, day, month, day_of_week = cron_parts
        trigger = CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
            timezone="UTC",
        )

    scheduler.add_job(
        _run_refresh,
        trigger=trigger,
        id="daily_refresh",
        name="Daily tender refresh",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # Daily digest at 10:30 UTC = 11:30 BST
    # Runs 15 min after the 10:15 refresh completes — cache is always populated
    scheduler.add_job(
        _run_digest,
        trigger=CronTrigger(hour=10, minute=30, timezone="UTC"),
        id="daily_digest",
        name="Daily tender digest",
        replace_existing=True,
        misfire_grace_time=300,
    )
    scheduler.start()
    logger.info(
        "Scheduler started — next refresh: %s",
        scheduler.get_job("daily_refresh").next_run_time,
    )


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


def next_run_time() -> str | None:
    job = scheduler.get_job("daily_refresh")
    if job and job.next_run_time:
        return job.next_run_time.isoformat()
    return None