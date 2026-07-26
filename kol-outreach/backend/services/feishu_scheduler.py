"""Database-backed Feishu delivery and reconciliation scheduler."""
import logging
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler

from config import settings
from services.feishu_push import (
    enqueue_reconciliation,
    process_due_tasks,
)

logger = logging.getLogger(__name__)
_scheduler: Optional[BackgroundScheduler] = None


def _delivery_job() -> None:
    try:
        result = process_due_tasks()
        if result["processed"] or result["failed"]:
            logger.info("飞书任务轮询完成: %s", result)
    except Exception:
        logger.exception("飞书任务轮询异常")


def _reconciliation_job() -> None:
    try:
        queued = enqueue_reconciliation()
        logger.info("飞书全量对账已入队: %s 个达人", queued)
    except Exception:
        logger.exception("飞书全量对账入队异常")


def start_feishu_scheduler() -> Optional[BackgroundScheduler]:
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler
    if not settings.feishu_is_configured:
        logger.info("飞书同步未配置，持久任务保留但调度器不启动")
        return None

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        _delivery_job,
        "interval",
        seconds=settings.FEISHU_SYNC_INTERVAL_SECONDS,
        id="feishu-delivery",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=settings.FEISHU_SYNC_INTERVAL_SECONDS,
    )
    _scheduler.add_job(
        _reconciliation_job,
        "interval",
        seconds=settings.FEISHU_RECONCILE_INTERVAL_SECONDS,
        id="feishu-reconciliation",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=settings.FEISHU_RECONCILE_INTERVAL_SECONDS,
    )
    _scheduler.start()
    # Queue reconciliation immediately; network delivery remains in the
    # scheduler so application startup is never blocked by Feishu.
    _reconciliation_job()
    logger.info(
        "飞书持久同步已启动: delivery=%ss reconcile=%ss",
        settings.FEISHU_SYNC_INTERVAL_SECONDS,
        settings.FEISHU_RECONCILE_INTERVAL_SECONDS,
    )
    return _scheduler


def stop_feishu_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None
