"""专用爬取脚本：仅走 ScrapeCreators 端到端抓取，跳过慢速的 YouTube HTML 发现/enrich。

为什么需要这个脚本：
  pipeline.run_crawl 总是先跑 _discover（YouTube HTML 搜索，69+查询×3变体），
  YouTube HTML 反爬让 httpx 抓取很慢甚至卡死，而 ScrapeCreators 阶段要等它跑完。
  本脚本直接调 _discover_via_scrapecreators（已覆盖 YouTube/TikTok/Instagram 三平台
  的搜索+profile+邮箱+国家），跳过 HTML 路径，速度快、数据全。

流程：
  _discover_via_scrapecreators → _mx_validate（有邮箱的） → _finalize_rows → upsert_candidates 写库

用法（在 backend/ 目录）::

    # 爬 Hypic + SCRL
    python -m scripts.crawl_scrapecreators_only --products Hypic SCRL

    # 只爬某个产品
    python -m scripts.crawl_scrapecreators_only --products Hypic

    # 不写库（干跑，只看发现规模 + 打印前几条）
    python -m scripts.crawl_scrapecreators_only --products Hypic --no-commit --limit-print 10
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from datetime import datetime

from db import SessionLocal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# 屏蔽 httpx 的逐请求 INFO 日志（爬取会发上千次请求，逐行打印会淹没真正的进度）。
logging.getLogger("httpx").setLevel(logging.WARNING)


async def crawl_sc_only(products: list[str], commit: bool, batch: str) -> dict:
    """仅 ScrapeCreators 阶段 + 后续 MX/入库。复用 pipeline 内部函数。"""
    from services.crawler.fetcher import HttpxFetcher
    from services.crawler.pipeline import (
        _discover_via_scrapecreators,
        _finalize_rows,
        _mx_validate,
    )

    last_phase = {"v": None}

    def on_progress(done: int, total: int, phase: str) -> None:
        if last_phase["v"] != phase or done % 12 == 0 or done == total:
            print(f"  [{phase}] {done}/{total}", flush=True)
            last_phase["v"] = phase

    fetcher = HttpxFetcher()
    stats: dict = {
        "products": products,
        "batch": batch,
        "mode": "ScrapeCreators-only",
        "started_at": datetime.utcnow().isoformat(),
    }
    try:
        # 阶段：ScrapeCreators 端到端（搜索 → profile → 构建候选行）
        rows = await _discover_via_scrapecreators(products, [], on_progress)
        stats["scrapecreators_rows"] = len(rows)
        stats["with_email"] = sum(1 for r in rows if r.get("contact_email"))
        print(f"  发现候选 {len(rows)} 行，含邮箱 {stats['with_email']} 行", flush=True)

        # 阶段：MX 校验（仅对有邮箱的行）
        if any(r.get("contact_email") for r in rows):
            await _mx_validate(fetcher, rows, on_progress)
        stats["mx_validated"] = sum(1 for r in rows if r.get("_mx_valid") is True)

        # 阶段：清洗
        cleaned = _finalize_rows(rows)

        # 阶段：入库
        if commit:
            from scripts.import_kol_candidate import upsert_candidates

            db = SessionLocal()
            try:
                up_stats = upsert_candidates(cleaned, db, batch, source_label="ScrapeCreators", commit=True)
                stats.update(up_stats)
            finally:
                db.close()
        else:
            stats["candidate_total"] = len(cleaned)

        stats["finished_at"] = datetime.utcnow().isoformat()
        return stats
    finally:
        await fetcher.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description="ScrapeCreators-only 爬取（跳过 YouTube HTML）")
    parser.add_argument("--products", nargs="+", required=True, help="产品名，如 Hypic SCRL")
    parser.add_argument("--no-commit", action="store_true", help="干跑，不写库")
    parser.add_argument("--limit-print", type=int, default=0, help="干跑时打印前 N 条候选样本")
    parser.add_argument("--batch", default=None, help="批次标签，默认按时间生成")
    args = parser.parse_args()

    batch = args.batch or f"sc-crawl-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    commit = not args.no_commit

    print(f"=== ScrapeCreators-only 爬取 ===")
    print(f"产品: {args.products} | 写库: {commit} | 批次: {batch}")
    t0 = time.time()

    stats = asyncio.run(crawl_sc_only(args.products, commit, batch))
    elapsed = time.time() - t0
    stats["elapsed_seconds"] = round(elapsed, 1)

    print(f"\n=== 完成，耗时 {elapsed:.0f}s ===")
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
