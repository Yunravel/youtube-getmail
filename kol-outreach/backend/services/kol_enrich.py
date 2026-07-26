"""回信建档 KOL 的画像补全。

回信自动建档（webhook / sync-replies）只写入 name + email，频道画像字段
（subscribers / country / niche / channel_url）全部为空。本模块用 KOL 的
name 去 YouTube 搜频道，靠 **about 页简介里出现该 KOL 的 email** 来确认
身份，命中后才解析该 about 页并填空式回写。

宁可漏不要错：邮箱在 about 页没公开（或 name 是显示名/人名）时不命中，
保持空，不臆测回填。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional
from urllib.parse import quote

from db import SessionLocal
from models import Kol, Message, Thread
from services.crawler import normalize, youtube

logger = logging.getLogger(__name__)

# 单个 KOL 最多检验多少个候选频道，避免刷爆代理池 / 被 YouTube 限流。
MAX_CANDIDATES = 5

# 只有这些状态的回信 KOL 才值得补画像（已结束/黑名单不动）。
ENRICHABLE_STATUSES = ("in_conversation", "sent")

# about 页太短判定为反爬/失败，与 pipeline._process 的阈值一致。
_MIN_ABOUT_LEN = 5000


async def _find_channel_by_email(fetcher, name: str, email: str) -> tuple[Optional[str], str]:
    """用 name 搜 YouTube，在候选频道的 about 页简介里找 email 确认身份。

    返回 ``(handle, about_html)``：handle 形如 ``/@SomeChannel``；
    无命中返回 ``(None, "")``。handle 已做邮箱验证，可放心回填。
    """
    if not name or not email:
        return None, ""
    target = email.lower()
    query = quote(name)
    search_url = f"https://www.youtube.com/results?search_query={query}"
    search_html = await fetcher.fetch_text(search_url)
    if not search_html:
        return None, ""
    handles = youtube.handles_from_search(search_html)[:MAX_CANDIDATES]
    for handle in handles:
        about_url = f"https://www.youtube.com{handle}/about"
        about_html = await fetcher.fetch_text(about_url)
        if not about_html or len(about_html) < _MIN_ABOUT_LEN:
            continue
        description = youtube.full_channel_description(about_html)
        emails = youtube.extract_emails(description)
        if target in emails:
            return handle, about_html
    return None, ""


def _parse_about(handle: str, about_html: str) -> dict:
    """从已确认命中的 about 页解析画像字段。"""
    title = youtube.meta_content(about_html, "title").replace(" - YouTube", "").strip()
    description = youtube.full_channel_description(about_html)
    subscriber_text = youtube.field_from_html(about_html, "subscriberCountText")
    followers = normalize.parse_subscriber_count(subscriber_text)
    country, _ = normalize.resolve_country(about_html, description, title)
    niche = youtube.channel_type(title, description)
    return {
        "channel_url": f"https://www.youtube.com{handle}",
        "subscribers": followers,
        "country": country or None,
        "niche": niche or None,
        "content_category": niche or None,
        "social_handle": handle.lstrip("/@"),
        "profile_url": f"https://www.youtube.com{handle}",
        "source": "reply_enrich",
    }


async def _enrich_async(kol_id: int) -> Optional[dict]:
    """异步主体：重载 KOL → 守卫 → 搜索验证 → 解析。返回字段 dict 或 None。

    用独立 SessionLocal（调用方往往是 BackgroundTask，请求 session 已关闭）。
    注意：不在此处 commit 写库，把 dict 交回同步入口统一回写，便于单测 mock。
    """
    db = SessionLocal()
    try:
        kol = db.query(Kol).get(kol_id)
        if not kol:
            return None
        if not _should_enrich(kol):
            return None
        if not kol.email or "@" not in kol.email:
            return None

        # 懒加载 fetcher：测试可 monkeypatch 模块级 _new_fetcher 注入假实现。
        fetcher = _new_fetcher()
        handle, about_html = await _find_channel_by_email(fetcher, kol.name, kol.email)
        if not handle:
            logger.info("回信 KOL 画像补全未命中: kol_id=%s name=%s", kol_id, kol.name)
            return None
        return _parse_about(handle, about_html)
    finally:
        db.close()


def _new_fetcher():
    """构造 HttpxFetcher，继承全局代理池配置。单测可覆盖。"""
    from services.crawler.fetcher import HttpxFetcher
    return HttpxFetcher()


def _should_enrich(kol: Kol) -> bool:
    """守卫：仅回信类、且 channel_url 为空的 KOL 才补。"""
    if kol.status not in ENRICHABLE_STATUSES:
        return False
    if (kol.channel_url or "").strip():
        return False
    return True


def _apply_fill(kol: Kol, fields: dict) -> int:
    """填空式回写：仅当字段有值且目标列为空时写。返回写入字段数。"""
    changed = 0
    for key, value in fields.items():
        if value is None:
            continue
        if getattr(kol, key, None):
            continue
        setattr(kol, key, value)
        changed += 1
    return changed


def enrich_reply_kol(kol_id: int) -> None:
    """同步入口（供 FastAPI BackgroundTask / 线程池调用）。

    跑一次 YouTube 搜索+邮箱验证，命中则填空式回写并 commit。
    任何失败都吞掉只记日志，绝不影响回信主流程。
    """
    try:
        fields = _run_async_safely(_enrich_async(kol_id))
    except Exception:
        logger.exception("回信 KOL 画像补全异常: kol_id=%s", kol_id)
        return
    if not fields:
        return

    db = SessionLocal()
    try:
        kol = db.query(Kol).get(kol_id)
        if not kol or not _should_enrich(kol):
            # 命中后这段时间画像可能已被其它途径补上，二次守卫避免覆盖。
            return
        changed = _apply_fill(kol, fields)
        if changed:
            db.commit()
            logger.info(
                "回信 KOL 画像补全完成: kol_id=%s name=%s 写入 %d 字段 channel=%s",
                kol_id, kol.name, changed, fields.get("channel_url"),
            )
    except Exception:
        db.rollback()
        logger.exception("回信 KOL 画像补全写库失败: kol_id=%s", kol_id)
        return
    finally:
        db.close()
    if changed:
        _requeue_feishu_sync(kol_id)


def _requeue_feishu_sync(kol_id: int) -> None:
    """画像补全落库后，重新入队该 KOL 最新一封回信的飞书同步。

    没有这步，先按空画像推出去的飞书行会一直停留在「待补全/待采集」占位符，
    直到下一次全量对账才被纠正（2026-07-27 排查的根因之一）。
    enqueue_message_sync 自带自动回复/休假信过滤，失败只记日志不影响补全结果。
    """
    try:
        db = SessionLocal()
        try:
            row = (
                db.query(Message.id)
                .join(Thread, Message.thread_id == Thread.id)
                .filter(Thread.kol_id == kol_id, Message.direction == "inbound")
                .order_by(Message.received_at.desc(), Message.id.desc())
                .first()
            )
        finally:
            db.close()
        if not row:
            return
        from services.feishu_push import enqueue_message_sync

        enqueue_message_sync(row[0])
    except Exception:
        logger.exception("画像补全后飞书刷新入队失败: kol_id=%s", kol_id)


def _run_async_safely(coro):
    """运行协程，兼容「调用方已有运行中事件循环」的场景（BackgroundTask 内）。

    仿 pipeline.run_crawl 的 thread-fallback 模式：检测到运行中的循环时，
    退到独立线程新建循环跑，避免 "asyncio.run() cannot be called from a
    running event loop"。
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import threading

            box: dict = {}
            exc: list[BaseException] = []

            def _runner():
                try:
                    new_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(new_loop)
                    box["result"] = new_loop.run_until_complete(coro)
                    new_loop.close()
                except BaseException as e:  # noqa: BLE001 - 透传给外层
                    exc.append(e)

            t = threading.Thread(target=_runner)
            t.start()
            t.join()
            if exc:
                raise exc[0]
            return box.get("result")
    except RuntimeError:
        # 无当前事件循环（普通同步线程），走标准路径。
        pass
    return asyncio.run(coro)
