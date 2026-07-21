"""定时 IMAP 附件同步：每 10 分钟扫所有邮箱的未读邮件抓附件。

补 Snov webhook 不传附件的缺口。手动触发（限定时间范围）走 api/attachments.py
的 _sync_jobs，不走这里。
"""
import logging
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler

from config import settings

logger = logging.getLogger(__name__)
_scheduler: Optional[BackgroundScheduler] = None


def _sync_job() -> None:
    # 惰性 import 避免与 API 路由的启动循环
    from services.attachment_sync import sync_all_mailboxes

    try:
        result = sync_all_mailboxes(mode="unread")
        logger.info(
            "IMAP 附件轮询完成: mailboxes=%s ok=%s failed=%s fetched=%s matched=%s attached=%s",
            result.get("mailboxes_total", 0),
            result.get("mailboxes_ok", 0),
            result.get("mailboxes_failed", 0),
            result.get("fetched", 0),
            result.get("matched", 0),
            result.get("attached", 0),
        )
    except Exception:
        logger.exception("IMAP 附件轮询失败")


def start_attachment_scheduler() -> Optional[BackgroundScheduler]:
    """启动定时轮询。无邮箱凭据或关闭开关时不启动。"""
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler
    if not settings.IMAP_SYNC_ENABLED:
        logger.info("IMAP 附件同步已关闭")
        return None

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        _sync_job,
        trigger="interval",
        seconds=settings.IMAP_SYNC_INTERVAL_SECONDS,
        id="imap-attachment-sync",
        replace_existing=True,
        coalesce=True,
        max_instances=1,            # 上一轮没跑完不启动新一轮
        misfire_grace_time=settings.IMAP_SYNC_INTERVAL_SECONDS,
    )
    _scheduler.start()
    logger.info("IMAP 附件轮询已启动: 每 %s 秒", settings.IMAP_SYNC_INTERVAL_SECONDS)
    return _scheduler


def stop_attachment_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None
