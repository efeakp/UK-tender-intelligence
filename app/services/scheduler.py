"""
Background scheduler — runs a daily tender refresh using APScheduler.
Default schedule: 10:15 UTC daily (configurable via REFRESH_CRON in .env).
"""
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.dependencies import cache, CACHE_KEY_TENDERS, update_all_source_meta, get_prev_tenders, set_prev_tenders

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="UTC")


async def _run_digest():
    from app.routers.digest import run_scheduled_digest
    logger.info("Running scheduled digest at %s", datetime.now(timezone.utc).isoformat())
    try:
        await run_scheduled_digest()
    except Exception as e:
        logger.error("Scheduled digest failed: %s", e, exc_info=True)


async def _run_refresh():
    from app.services.aggregator import refresh_all

    logger.info("Scheduled refresh starting at %s", datetime.now(timezone.utc).isoformat())
    try:
        previous_tenders = get_prev_tenders()
        tenders, errors, raw_source_counts = await refresh_all(
            days_back=settings.refresh_days_back,
            previous_tenders=previous_tenders,
        )

        cache.set(CACHE_KEY_TENDERS, tenders, ttl_minutes=settings.cache_ttl_minutes)
        set_prev_tenders(tenders)
        update_all_source_meta(raw_source_counts, errors)

        logger.info(
            "Scheduled refresh complete: %d tenders (window: %d days) — %s",
            len(tenders),
            settings.refresh_days_back,
            " | ".join(f"{k}:{v}" for k, v in raw_source_counts.items()),
        )

    except Exception as e:
        logger.error("Scheduled refresh failed: %s", e, exc_info=True)


def start_scheduler():
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
            minute=minute, hour=hour, day=day,
            month=month, day_of_week=day_of_week, timezone="UTC",
        )

    scheduler.add_job(
        _run_refresh,
        trigger=trigger,
        id="daily_refresh",
        name="Daily tender refresh",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # Digest runs 15 min after the 10:15 refresh — cache is always populated
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
