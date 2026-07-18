"""采集字段规范化与候选行组装。

把采集各阶段产出的零散字段（handle/email/country/...）组装成
``kol_candidate`` 的 ORM 字段 dict，供 :func:`pipeline.run_crawl` 喂给
:func:`scripts.import_kol_candidate.upsert_candidates` 入库。

字段名对齐 ORM（platform/account/contact_email/followers/...），不是中文 Excel 列名。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from services.crawler import youtube


def parse_subscriber_count(raw: str) -> int | None:
    """YouTube subscriberCountText 形如 '1.2M subscribers' → 1200000。

    失败返回 None。
    """
    if not raw:
        return None
    s = raw.lower().replace(",", "").replace("subscribers", "").strip()
    mult = 1
    if s.endswith("k"):
        s, mult = s[:-1], 1_000
    elif s.endswith("m"):
        s, mult = s[:-1], 1_000_000
    try:
        return max(0, int(float(s) * mult))
    except ValueError:
        return None


def build_candidate_row(
    *,
    platform: str,
    account: str,
    profile_url: str,
    contact_email: str | None,
    email_is_business: bool = False,
    email_source_url: str | None = None,
    email_mx_valid: bool | None = None,
    country: str = "Unknown",
    country_confidence: str = "未知",
    followers: int | None = None,
    avg_views: int | None = None,
    language: str | None = None,
    account_type: str | None = None,
    fit_products: list[str] | None = None,
    recommend_product: str | None = None,
    discovery_queries: list[str] | None = None,
    discovery_method: str = "平台关键词发现",
    related_youtube: str | None = None,
    yt_about_source: str | None = None,
) -> dict[str, Any]:
    """组装一行 kol_candidate 字段 dict。

    多值字段（fit_products/discovery_queries）按业务约定拼成字符串。
    所有字符串字段做了基本清洗，未做列宽截断（upsert_candidates 内部对晋升 kol
    的字段有切片；candidate 直接写库的 varchar 列请保持合理长度，调用方负责）。
    """
    # 联系邮箱：单邮箱（多邮箱场景由调用方多次调用本函数，每邮箱一行）
    email_status = None
    if contact_email:
        email_status = "公开商务邮箱" if email_is_business else "公开邮箱"

    collect_status_parts: list[str] = []
    if email_mx_valid is True:
        collect_status_parts.append("MX有效")
    elif email_mx_valid is False:
        collect_status_parts.append("无MX记录")

    return {
        "source_row": None,
        "platform": platform,
        "account": account,
        "profile_url": profile_url or None,
        "related_youtube": related_youtube or None,
        "yt_about_source": yt_about_source or None,
        "discovery_method": discovery_method,
        "fit_product": "，".join(fit_products) if fit_products else None,
        "recommend_product": recommend_product,
        "hit_keyword_count": len(discovery_queries) if discovery_queries else None,
        "keyword_note": "；".join(discovery_queries[:6]) if discovery_queries else None,
        "crawl_priority": None,
        "email_crawler": contact_email,  # 爬虫回填列与主邮箱同步
        "email_source_url": email_source_url or None,
        "email_verify_status": ("MX有效" if email_mx_valid else "待爬取") if contact_email else "待爬取",
        "country_region": country if country != "Unknown" else None,
        "language": language,
        "account_type": account_type,
        "followers": followers,
        "avg_views": avg_views,
        "review_status": "待复核",
        "remark": None,
        "contact_email": contact_email,
        "email_status": email_status,
        "email_source": "公开主页简介" if contact_email else None,
        "other_links": None,
        "collect_status": ("；".join(collect_status_parts) if collect_status_parts else None) if contact_email else None,
        "collected_at": datetime.utcnow(),
    }


def resolve_country(html: str, description: str, title: str) -> tuple[str, str]:
    """国家推断双策略：平台声明优先，否则简介推断。返回 (国家, 置信度)。

    置信度：平台声明 / 简介推断 / 未知（与 Node 一致）。
    """
    stated = youtube.explicit_country(html)
    if stated:
        return stated, "平台声明"
    inferred = youtube.inferred_country(description, title)
    if inferred:
        return inferred, "简介推断"
    return "Unknown", "未知"
