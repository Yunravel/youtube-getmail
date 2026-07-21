"""历史附件链接回填：从 inbound 邮件正文提取网盘分享链接，补写 message.attachments。

背景：Snov webhook 不传结构化附件字段，KOL 的"附件"常以网盘链接形式塞在
正文里。新邮件入库时已由 webhook.py/snov.py 自动提取，本脚本用于补齐历史邮件。

用法：
  python -m scripts.backfill_attachments              # dry-run 预览
  python -m scripts.backfill_attachments --commit     # 实际写库

设计：
- 幂等：默认只处理 attachments 为空/无链接的 message（``--force`` 才重算覆盖）。
- 纯文本处理，不联网下载；提取逻辑与 webhook 入库时一致。
"""
from __future__ import annotations

import argparse
import logging
from typing import Callable, Optional

from db import SessionLocal
from models import Message
from services.attachments import extract_links_from_text, merge_attachments

logger = logging.getLogger(__name__)


def _has_link_attachment(existing) -> bool:
    """判断 attachments 列表里是否已有正文提取出的链接型附件。"""
    if not existing:
        return False
    return any(isinstance(item, dict) and item.get("url") for item in existing)


def run_backfill(
    thread_ids: Optional[list[int]] = None,
    commit: bool = False,
    force: bool = False,
    limit: Optional[int] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> dict:
    """扫历史 inbound 邮件，提取正文网盘链接写回 message.attachments。

    返回统计 dict。dry-run 时不写库，只统计可提取条数。
    """
    db = SessionLocal()
    try:
        query = db.query(Message).filter(
            Message.direction == "inbound",
            Message.body_text.isnot(None),
            Message.body_text != "",
        )
        if thread_ids:
            query = query.filter(Message.thread_id.in_(thread_ids))
        # 幂等判断放在 Python 层（JSON 列的 SQL 层过滤跨库不一致）
        all_messages = query.order_by(Message.received_at.desc()).all()
    finally:
        db.close()

    if not force:
        messages = [m for m in all_messages if not _has_link_attachment(m.attachments)]
    else:
        messages = all_messages
    if limit:
        messages = messages[:limit]

    total = len(messages)
    scanned = 0
    updated = 0
    skipped = 0
    found_links = 0
    samples: list[dict] = []

    if total == 0:
        return {"total": 0, "scanned": 0, "updated": 0, "skipped": 0,
                "found_links": 0, "dry_run": not commit, "samples": []}

    db = SessionLocal()
    try:
        for index, msg in enumerate(messages, start=1):
            scanned += 1
            if on_progress and (index % 50 == 0 or index == total):
                on_progress(index, total)

            links = extract_links_from_text(msg.body_text)
            if not links:
                skipped += 1
                continue

            found_links += len(links)
            merged = merge_attachments(msg.attachments or [], links)
            if len(samples) < 10:
                samples.append({
                    "message_id": msg.id,
                    "thread_id": msg.thread_id,
                    "links": [l["url"] for l in links],
                })

            if commit:
                msg.attachments = merged
            updated += 1

        if commit:
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    return {
        "total": total,
        "scanned": scanned,
        "updated": updated,
        "skipped": skipped,
        "found_links": found_links,
        "dry_run": not commit,
        "samples": samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="回填历史邮件正文里的网盘附件链接")
    parser.add_argument("--thread-ids", type=str, default="", help="逗号分隔的 thread_id，空则全部")
    parser.add_argument("--commit", action="store_true", help="实际写库（默认 dry-run 预览）")
    parser.add_argument("--force", action="store_true", help="重算覆盖（默认跳过已有链接附件的 message）")
    parser.add_argument("--limit", type=int, default=None, help="最多处理多少条（调试用）")
    args = parser.parse_args()

    thread_ids = None
    if args.thread_ids.strip():
        thread_ids = [int(x.strip()) for x in args.thread_ids.split(",") if x.strip()]

    mode = "写库" if args.commit else "预览(dry-run)"
    print(f"附件链接回填 [{mode}] force={args.force}")
    result = run_backfill(
        thread_ids=thread_ids,
        commit=args.commit,
        force=args.force,
        limit=args.limit,
        on_progress=lambda done, total: print(f"  扫描进度 {done}/{total}", flush=True),
    )
    print(f"\n结果: {result}")
    if result["samples"]:
        print("\n样本（最多前 10 条）:")
        for sample in result["samples"]:
            print(f"  message={sample['message_id']} thread={sample['thread_id']}")
            for url in sample["links"]:
                print(f"    - {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
