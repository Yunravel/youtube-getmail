"""Database-backed polling scheduler for automatic replies."""
import logging
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler

from config import settings
from services.auto_reply import process_due_replies, recover_interrupted_deliveries

logger = logging.getLogger(__name__)
_scheduler: Optional[BackgroundScheduler] = None


def start_auto_reply_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler
    recover_interrupted_deliveries()
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        process_due_replies, "interval", seconds=settings.AUTO_REPLY_POLL_SECONDS,
        id="auto-reply-dispatch", replace_existing=True, coalesce=True,
        max_instances=1, misfire_grace_time=settings.AUTO_REPLY_POLL_SECONDS,
    )
    _scheduler.start()
    logger.info("自动回复调度已启动: 每 %s 秒", settings.AUTO_REPLY_POLL_SECONDS)
    return _scheduler


def stop_auto_reply_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None
