"""Two-minute Snov polling job used to repair missed webhook events."""
import logging
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler

from config import settings
from db import SessionLocal

logger = logging.getLogger(__name__)
_scheduler: Optional[BackgroundScheduler] = None


def _sync_job() -> None:
    # Import lazily to avoid a startup import cycle with the API router.
    from api.snov import sync_historical_replies

    db = SessionLocal()
    try:
        result = sync_historical_replies(db)
        logger.info(
            "Snov periodic sync complete: campaigns=%s created=%s duplicates=%s skipped=%s errors=%s",
            result.get("campaigns", 0),
            result.get("created_messages", 0),
            result.get("duplicates", 0),
            result.get("skipped", 0),
            len(result.get("errors", [])),
        )
    except Exception:
        db.rollback()
        logger.exception("Snov periodic sync failed")
    finally:
        db.close()


def start_snov_scheduler() -> Optional[BackgroundScheduler]:
    """Start one coalescing polling job when API credentials are configured."""
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler
    if not settings.SNOV_SYNC_ENABLED:
        logger.info("Snov periodic sync is disabled")
        return None
    if not settings.SNOV_CLIENT_ID or not settings.SNOV_CLIENT_SECRET:
        logger.warning("Snov periodic sync not started: API credentials are missing")
        return None

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        _sync_job,
        trigger="interval",
        seconds=settings.SNOV_SYNC_INTERVAL_SECONDS,
        id="snov-replies-sync",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=settings.SNOV_SYNC_INTERVAL_SECONDS,
    )
    _scheduler.start()
    logger.info("Snov periodic sync started: every %s seconds", settings.SNOV_SYNC_INTERVAL_SECONDS)
    return _scheduler


def stop_snov_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None
