"""AI 总结 KOL 社会身份（position 字段）。

目标：为 position 为空的 KOL 用 AI 生成中文粗粒度身份标签（如「AI 创作博主」
「英国家庭生活博主」），填入报价单 F 列「社会身份」。

为什么单独一个脚本
-----------------
- :mod:`scripts.backfill_kol_fields` 只做"爬"（followers/country/platform/URL）。
- position 需要先爬简介（拿素材），再让 AI 总结，跟字段补全的爬取阶段性质不同。
- contact_notes 里已有「达人画像」的（38 个）已由本脚本之前一步直接正则提取，
  本脚本只处理剩余的 position 空值（无现成素材）。

流程
----
1. 从 DB 取 position 为空的 KOL（email 非空）
2. 按 platform 复用 fetcher 抓主页/about 页 → 拿 title + description
3. 调 DeepSeek（复用 services.ai_profile 客户端）→ 中文粗粒度身份（≤10 字）
4. dry-run 预览 / commit 写库

CLI
---
  python -m scripts.backfill_kol_position --limit 5            # 预览
  python -m scripts.backfill_kol_position --commit             # 全量写库
  python -m scripts.backfill_kol_position --commit --force     # 覆盖已有值
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import warnings
from typing import Optional

if sys.platform == "win32":
    warnings.filterwarnings("ignore", category=ResourceWarning)

from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.orm import Session

from db import SessionLocal
from models import Kol
from services.crawler import normalize, youtube
from services.crawler.fetcher import HttpxFetcher

# 复用 backfill_kol_fields 的 Playwright HTML 抓取器（取 page.content 而非 inner_text）
from scripts.backfill_kol_fields import (
    _PwHtmlFetcher,
    _profile_or_channel_url,
    _youtube_about_url,
)
from services.crawler.profile_parsers import _meta

logger = logging.getLogger(__name__)

# DeepSeek 客户端（与 services.ai_profile 同源，复用 OPENAI_* 配置）
from services.ai_profile import _get_client
from config import settings

# 允许的粗粒度领域标签（白名单）。AI 返回的标签若不在此列表，会被规范化到最近的一个。
# 这样保证 position 字段值受控（不会出现奇怪的 AI 输出）。
_ALLOWED_DOMAINS = [
    "AI 创作", "科技", "美妆", "时尚", "健身", "美食", "旅行",
    "教育", "搞笑", "生活方式", "音乐", "商业财经", "亲子家庭",
    "健康养生", "艺术创意", "汽车", "体育", "游戏", "摄影",
    "情侣情感", "家居生活", "宠物", "读书学习", "UGC 创作",
]

# 粗粒度 prompt：要求 AI 只输出白名单内的标签（中文，≤6 字）
# 关键：要求 AI 区分「创作者本人在做 AI 内容」和「创作者领域是 X 但碰巧提到 AI 工具」。
# 否则大量 Pippit/Dreamina 推广对象的简介都提到 AI，会被一律标成 "AI 创作"。
_SYSTEM_PROMPT = """你是 KOL 社会身份分类器。给你一个博主的标题和简介，输出一个中文粗粒度身份标签，表示博主**内容聚焦的领域**。

重要：很多博主简介里会提到 "AI" 或使用 AI 工具，但他们的内容领域可能是旅行、教育、美妆等。
你要判断的是**博主创作的内容主要讲什么主题**，而不是他们用了什么工具。

例如：
- "AI image and video generation tutorials" → AI 创作（内容就是教 AI）
- "UK travel vlogger, sharing weekend trips" → 旅行（虽然可能用 AI 做封面）
- "PhD student sharing study tips" → 读书学习
- "Beauty reviewer testing skincare" → 美妆
- "UGG creator for brands" → UGC 创作

只能从以下标签中选一个最贴近的，不要编造新标签：
""" + "、".join(_ALLOWED_DOMAINS) + """

只输出标签本身（如「旅行」「美妆」），不要任何解释、标点、引号。无法判断时输出「生活方式」（兜底）。"""

# niche_hint（关键词规则）→ 中文标签 映射（不调 AI 的快路径）
# 与 services.crawler.profile_parsers._NICHE_KEYWORDS 顺序一致
_KEYWORD_TO_CN = {
    "tech": "科技",
    "gaming": "游戏",
    "beauty": "美妆",
    "fashion": "时尚",
    "fitness": "健身",
    "food": "美食",
    "travel": "旅行",
    "education": "教育",
    "comedy": "搞笑",
    "lifestyle": "生活方式",
    "music": "音乐",
    "business": "商业财经",
    "parenting": "亲子家庭",
    "health": "健康养生",
    "art": "艺术创意",
    "auto": "汽车",
    "sports": "体育",
}


def _classify_by_ai(title: str, description: str) -> Optional[str]:
    """调 DeepSeek 把 title + description 分类成中文粗粒度标签。

    失败返回 None。

    注意：IG 的 og:description 是 "X Followers, Y Following..." 垃圾模板，
    **真实身份信息在 og:title 里**（如 "Amy Tucker | UK UGC Creator ..."）。
    X 的 og:description 才是真实简介。所以本函数两个都喂给 AI，让它自己挑。
    """
    client = _get_client()
    if not client:
        return None
    # HTML 实体解码（IG 的 &amp; 等）
    title = (title or "").replace("&amp;", "&").replace("&#39;", "'")
    description = (description or "").replace("&amp;", "&").replace("&#39;", "'")
    text = f"标题：{title or '(无)'}\n简介：{description or '(无)'}"
    try:
        resp = client.chat.completions.create(
            model=settings.OPENAI_MODEL_INTENT,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            # DeepSeek-v4 是推理模型：reasoning_tokens 占大量配额。
            # 实测简单分类需要 ~200 推理 token + 5 输出 token，给 800 安全。
            max_tokens=800,
            temperature=0,
        )
        raw = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.debug("AI classify failed: %s", e)
        return None
    # 规范化到白名单：精确命中优先，否则子串包含
    for tag in _ALLOWED_DOMAINS:
        if raw == tag:
            return tag
    for tag in _ALLOWED_DOMAINS:
        if tag in raw or raw in tag:
            return tag
    return None


# ---------------------------------------------------------------------------
# 阶段 1：抓主页/about 页 → 取 title + description
# ---------------------------------------------------------------------------


async def _fetch_material_youtube(http_fetcher: HttpxFetcher, kol: Kol) -> tuple[str, str]:
    """YouTube：抓 about 页，返回 (title, description)。失败返回 ('','')。"""
    about_url = _youtube_about_url(kol)
    if not about_url:
        return "", ""
    html = await http_fetcher.fetch_text(about_url)
    if not html or len(html) < 5000:
        return "", ""
    title = youtube.meta_content(html, "title").replace(" - YouTube", "").strip()
    description = youtube.full_channel_description(html)
    return title, description


async def _fetch_material_social(
    pw: _PwHtmlFetcher, kol: Kol
) -> tuple[str, str]:
    """IG/TT/X：抓主页 HTML，返回 (title, description)。失败返回 ('','')。"""
    url = _profile_or_channel_url(kol)
    if not url:
        return "", ""
    html = await pw.fetch_html(url)
    if not html or len(html) < 1000:
        return "", ""
    title = _meta(html, "og:title")
    description = _meta(html, "og:description")
    return title, description


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _collect_targets(db: Session, limit: Optional[int]) -> list[Kol]:
    """position 为空 + 有 email + 有 platform（有 URL 才能爬素材）的 KOL。"""
    q = db.query(Kol).filter(
        Kol.email.isnot(None),
        Kol.email != "",
        (Kol.position.is_(None)) | (Kol.position == ""),
        Kol.platform.isnot(None),
        Kol.platform != "",
    )
    # 必须有 URL（profile_url 或 channel_url）
    q = q.filter(
        ((Kol.profile_url.isnot(None)) & (Kol.profile_url != ""))
        | ((Kol.channel_url.isnot(None)) & (Kol.channel_url != ""))
    )
    rows = q.order_by(Kol.id.asc()).all()
    if limit:
        rows = rows[:limit]
    return rows


async def _gather_materials(
    kols: list[Kol], *, on_progress=None
) -> dict[int, tuple[str, str]]:
    """并发抓所有 KOL 的素材。返回 {kol_id: (title, description)}。"""
    youtube_kols = [k for k in kols if (k.platform or "").lower() == "youtube"]
    social_kols = [k for k in kols if (k.platform or "").lower() != "youtube"]
    materials: dict[int, tuple[str, str]] = {}

    # YouTube：httpx（快）
    if youtube_kols:
        http_fetcher = HttpxFetcher()
        try:
            done = 0
            total = len(youtube_kols)

            async def _one(kol: Kol):
                nonlocal done
                title, desc = await _fetch_material_youtube(http_fetcher, kol)
                materials[kol.id] = (title, desc)
                done += 1
                if on_progress and (done % 10 == 0 or done == total):
                    on_progress(done, total, "fetch_youtube")

            from services.crawler.fetcher import gather_pool
            await gather_pool(youtube_kols, 6, _one)
        finally:
            await http_fetcher.aclose()

    # IG/TT/X：Playwright（慢）
    if social_kols:
        pw = _PwHtmlFetcher()
        try:
            done = 0
            total = len(social_kols)

            async def _one2(kol: Kol):
                nonlocal done
                title, desc = await _fetch_material_social(pw, kol)
                materials[kol.id] = (title, desc)
                done += 1
                if on_progress and (done % 5 == 0 or done == total):
                    on_progress(done, total, "fetch_social")

            from services.crawler.fetcher import gather_pool
            await gather_pool(social_kols, 3, _one2)
        finally:
            await pw.aclose()

    return materials


def _classify_all(
    kols: list[Kol], materials: dict[int, tuple[str, str]], *, workers: int = 6
) -> dict[int, Optional[str]]:
    """线程池并发调 AI 分类。返回 {kol_id: chinese_label_or_None}。"""
    out: dict[int, Optional[str]] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {}
        for kol in kols:
            title, desc = materials.get(kol.id, ("", ""))
            if not (title or desc):
                continue  # 没素材的等会儿统一标 no_material
            fut = ex.submit(_classify_by_ai, title, desc)
            futures[fut] = kol.id
        for fut in list(futures):
            kol_id = futures[fut]
            try:
                out[kol_id] = fut.result()
            except Exception as e:
                logger.debug("classify %s failed: %s", kol_id, e)
                out[kol_id] = None
    return out


def run_backfill(
    *,
    commit: bool = False,
    force: bool = False,
    limit: Optional[int] = None,
    workers: int = 6,
    on_progress=None,
) -> dict:
    """执行 AI 社会身份回填。返回统计 dict。"""
    db = SessionLocal()
    stats: dict = {
        "commit": commit,
        "force": force,
        "limit": limit,
        "started_at": None,
        "targets": 0,
        "fetched": 0,
        "classified": 0,
        "updated": 0,
        "skipped_no_material": 0,
        "skipped_ai_failed": 0,
    }
    try:
        kols = _collect_targets(db, limit)
        stats["targets"] = len(kols)
        if not kols:
            return stats

        # 阶段 1：抓素材
        materials = asyncio.run(_gather_materials(kols, on_progress=on_progress))
        stats["fetched"] = sum(1 for t, d in materials.values() if t or d)

        # 阶段 2：AI 分类
        classifications = _classify_all(kols, materials, workers=workers)

        # 阶段 3：写库
        updated = 0
        no_material = 0
        ai_failed = 0
        for kol in kols:
            title, desc = materials.get(kol.id, ("", ""))
            if not (title or desc):
                no_material += 1
                continue
            label = classifications.get(kol.id)
            if not label:
                ai_failed += 1
                continue
            if force or not kol.position:
                kol.position = label
                updated += 1
        stats["classified"] = sum(1 for v in classifications.values() if v)
        stats["updated"] = updated
        stats["skipped_no_material"] = no_material
        stats["skipped_ai_failed"] = ai_failed

        if commit:
            db.commit()
        else:
            db.rollback()
    finally:
        db.close()

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="AI 总结 KOL 社会身份")
    parser.add_argument("--commit", action="store_true", help="实际写库（默认 dry-run）")
    parser.add_argument("--force", action="store_true", help="覆盖非空字段")
    parser.add_argument("--limit", type=int, default=None, help="最多处理多少条")
    parser.add_argument("--workers", type=int, default=6, help="AI 调用并发数")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    def _progress(done, total, phase):
        print(f"  [{phase}] {done}/{total}", flush=True)

    stats = run_backfill(
        commit=args.commit, force=args.force, limit=args.limit,
        workers=args.workers, on_progress=_progress,
    )
    mode = "写库" if stats["commit"] else "预览(dry-run)"
    print(f"\n=== AI 社会身份回填 [{mode}] ===")
    print(f"  目标: {stats['targets']}")
    print(f"  抓到素材: {stats['fetched']}")
    print(f"  AI 分类成功: {stats['classified']}")
    print(f"  写入 position: {stats['updated']}")
    print(f"  无素材跳过: {stats['skipped_no_material']}")
    print(f"  AI 失败跳过: {stats['skipped_ai_failed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
