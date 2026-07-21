"""定时采集任务（APScheduler）。

复刻 snov_scheduler.py 模式：进程内 BackgroundScheduler，cron 触发，
单实例（max_instances=1，避免重叠）。重启即丢（无持久化），靠幂等可重跑。

启用：在 .env 设 CRAWLER_SCHEDULE_CRON 为 cron 表达式（如 "0 2 * * *" 每天2点）。
默认关闭。建议低频运行 + 住宅代理池，避免被 YouTube 限流。
"""
import logging
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler

from config import settings
from db import SessionLocal

logger = logging.getLogger(__name__)
_scheduler: Optional[BackgroundScheduler] = None


def _scheduled_crawl_job() -> None:
    """定时触发的采集任务。按 CRAWLER_SCHEDULE_PRODUCTS 跑全量产品词表。"""
    products = [p.strip() for p in settings.CRAWLER_SCHEDULE_PRODUCTS.split(",") if p.strip()]
    if not products:
        logger.warning("定时采集跳过：CRAWLER_SCHEDULE_PRODUCTS 未配置")
        return

    from services.crawler import run_crawl

    db = SessionLocal()
    try:
        result = run_crawl(
            products,
            enable_enrich=True,
            enable_email=True,
            commit=True,
            db=db,
        )
        logger.info(
            "定时采集完成: products=%s discovered=%s raw_rows=%s with_email=%s "
            "candidate_inserted=%s kol_inserted=%s",
            products,
            result.get("discovered", 0),
            result.get("raw_rows", 0),
            result.get("with_email", 0),
            result.get("candidate_inserted", 0),
            result.get("kol_inserted", 0),
        )
    except Exception:
        db.rollback()
        logger.exception("定时采集任务失败")
    finally:
        db.close()


def start_crawler_scheduler() -> Optional[BackgroundScheduler]:
    """按 CRAWLER_SCHEDULE_CRON 启动定时采集。未配置则不启动。"""
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler
    cron = (settings.CRAWLER_SCHEDULE_CRON or "").strip()
    if not cron or cron.lower() in ("false", "off", "disabled"):
        logger.info("定时采集未启用（CRAWLER_SCHEDULE_CRON 为空）")
        return None

    # 解析 cron 表达式（5 段：分 时 日 月 周）
    parts = cron.split()
    if len(parts) != 5:
        logger.error("定时采集未启用：CRAWLER_SCHEDULE_CRON 不是合法 cron（需5段）: %r", cron)
        return None

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        _scheduled_crawl_job,
        trigger="cron",
        minute=parts[0], hour=parts[1],
        day=parts[2], month=parts[3], day_of_week=parts[4],
        id="crawler-scheduled-crawl",
        replace_existing=True,
        coalesce=True,        # 错过的多次只补跑一次
        max_instances=1,      # 同一时刻只允许一个采集任务
        misfire_grace_time=3600,
    )
    _scheduler.start()
    logger.info(
        "定时采集已启用: cron=%s products=%s",
        cron, settings.CRAWLER_SCHEDULE_PRODUCTS,
    )
    return _scheduler


def stop_crawler_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None
