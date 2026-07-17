"""AI 画像回填：从 KOL 回信正文提取画像/报价/合作条件，回填到 kol 表与 message.ai_analysis。

两种用法：
  1. CLI（手动/调试）:
       python scripts/backfill_kol_profile.py --dry-run
       python scripts/backfill_kol_profile.py --commit --workers 4
  2. import（被 HTTP 后台任务调用）:
       from scripts.backfill_kol_profile import run_backfill
       run_backfill(thread_ids=[1,2,3], commit=True, force=False, on_progress=fn)

设计原则：
- 幂等：默认只填空字段，不覆盖人工已填值（``--force`` 才覆盖）。
- 增量：``message.ai_analysis`` 是 JSON dict，本脚本只合并 4 个新 key
  (deliverables/payment_terms/usage_rights/creator_niche)，不动意向分析已有字段。
- 不碰意向分析：不修改 intent/intent_score/summary/suggested_action 等字段。
"""
from __future__ import annotations

import argparse
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

from db import SessionLocal
from models import Kol, Message
from services.ai_profile import extract_profile

logger = logging.getLogger(__name__)

# 回填到 kol 表的字段映射：(ai 字段名, kol 列名)
# 这些列是"画像"类，AI 提取后回填；社会身份/内容定位也回填到 position/niche（旧字段复用）。
_KOL_FIELD_MAP = [
    ("platform", "platform"),
    ("social_handle", "social_handle"),
    ("profile_url", "profile_url"),
    ("followers_count", "followers"),
    ("creator_niche", "position"),     # 旧 position 字段 Snov 不返回（实测 0 行），复用存社会身份
    ("content_focus", "niche"),        # niche 被 ai_personalize/send 真消费，AI 填内容定位也合理
]

# 增量合并进 message.ai_analysis 的字段（不动意向字段）
_AI_EXTRA_FIELDS = ("deliverables", "payment_terms", "usage_rights", "creator_niche", "content_focus")


def _collect_target_messages(db, thread_ids: Optional[list[int]] = None) -> list[dict]:
    """收集待处理的 message（取每个 thread 最新一封有正文的 inbound）。

    同一 thread 多封回信只处理最新一封，避免旧信覆盖新报价。
    """
    query = db.query(Message).filter(
        Message.direction == "inbound",
        Message.body_text.isnot(None),
        Message.body_text != "",
    )
    if thread_ids:
        query = query.filter(Message.thread_id.in_(thread_ids))

    messages = query.order_by(Message.thread_id, Message.received_at.desc()).all()
    # 每个 thread 只保留最新一封
    seen_threads: set[int] = set()
    items: list[dict] = []
    for msg in messages:
        if msg.thread_id in seen_threads:
            continue
        seen_threads.add(msg.thread_id)
        kol_name = msg.thread.kol.name if msg.thread and msg.thread.kol else None
        items.append({
            "message_id": msg.id,
            "thread_id": msg.thread_id,
            "kol_id": msg.thread.kol_id if msg.thread else None,
            "body": msg.body_text or "",
            "subject": msg.subject,
            "kol_name": kol_name,
        })
    return items


def _extract_one(item: dict) -> tuple[int, Optional[dict]]:
    """线程池 worker：提取单条。返回 (message_id, profile_or_None)。"""
    profile = extract_profile(
        email_body=item["body"],
        kol_name=item["kol_name"],
        subject=item["subject"],
    )
    return item["message_id"], profile


def run_backfill(
    thread_ids: Optional[list[int]] = None,
    commit: bool = False,
    force: bool = False,
    workers: int = 4,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> dict:
    """执行回填。返回统计 dict。

    Args:
        thread_ids: 限定这些 thread；None 则处理全部有正文的 inbound。
        commit: True 才写库；False 只预览。
        force: True 覆盖非空字段；False 只填空（幂等默认）。
        workers: 并发线程数。
        on_progress: 回调 (processed, total)，供 HTTP 进度条用。
    """
    db = SessionLocal()
    try:
        items = _collect_target_messages(db, thread_ids)
    finally:
        db.close()

    total = len(items)
    if total == 0:
        return {"total": 0, "extracted": 0, "kol_updated": 0, "msg_updated": 0, "skipped": 0}

    # 并发提取
    results: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(_extract_one, item) for item in items]
        for index, future in enumerate(as_completed(futures), start=1):
            msg_id, profile = future.result()
            if profile and profile.get("analysis_source") == "model":
                results[msg_id] = profile
            if on_progress:
                on_progress(index, total)

    if not commit:
        return {
            "total": total,
            "extracted": len(results),
            "kol_updated": 0,
            "msg_updated": 0,
            "skipped": total - len(results),
            "dry_run": True,
        }

    # 写库：kol 表增量填空 + message.ai_analysis 合并
    db = SessionLocal()
    kol_updated = 0
    msg_updated = 0
    try:
        for msg_id, profile in results.items():
            msg = db.query(Message).filter(Message.id == msg_id).first()
            if not msg:
                continue

            # 1) kol 表画像字段（仅填空，除非 force）
            kol_changed = False
            if msg.thread and msg.thread.kol:
                kol = msg.thread.kol
                for ai_field, kol_col in _KOL_FIELD_MAP:
                    value = profile.get(ai_field)
                    if value is None:
                        continue
                    # followers_count 转 int
                    if ai_field == "followers_count":
                        try:
                            value = int(float(str(value).replace(",", "")))
                            if value <= 0:
                                continue
                        except (ValueError, TypeError):
                            continue
                    current = getattr(kol, kol_col, None)
                    if force or not current:
                        setattr(kol, kol_col, value)
                        kol_changed = True
                # followers 也同步到 subscribers（旧冗余字段，保持一致）
                if kol_changed and kol.followers and not kol.subscribers:
                    kol.subscribers = kol.followers

            # 2) message.ai_analysis 增量合并（不动意向字段）
            existing = dict(msg.ai_analysis or {})
            ai_changed = False
            for field in _AI_EXTRA_FIELDS:
                value = profile.get(field)
                if value is None:
                    continue
                if force or existing.get(field) in (None, ""):
                    existing[field] = value
                    ai_changed = True
            if ai_changed:
                existing["profile_source"] = profile.get("analysis_source")
                existing["profile_model"] = profile.get("analysis_model")
                msg.ai_analysis = existing
                msg_updated += 1

            if kol_changed:
                kol_updated += 1

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    return {
        "total": total,
        "extracted": len(results),
        "kol_updated": kol_updated,
        "msg_updated": msg_updated,
        "skipped": total - len(results),
        "force": force,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="AI 回填 KOL 画像与报价")
    parser.add_argument("--thread-ids", type=str, default="", help="逗号分隔的 thread_id，空则全部")
    parser.add_argument("--commit", action="store_true", help="实际写库（默认 dry-run 预览）")
    parser.add_argument("--force", action="store_true", help="覆盖非空字段（默认只填空）")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    thread_ids = None
    if args.thread_ids.strip():
        thread_ids = [int(x.strip()) for x in args.thread_ids.split(",") if x.strip()]

    mode = "写库" if args.commit else "预览(dry-run)"
    print(f"AI 画像回填 [{mode}] force={args.force} workers={args.workers}")
    result = run_backfill(
        thread_ids=thread_ids,
        commit=args.commit,
        force=args.force,
        workers=args.workers,
        on_progress=lambda done, total: print(f"  提取进度 {done}/{total}", flush=True),
    )
    print(f"\n结果: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
