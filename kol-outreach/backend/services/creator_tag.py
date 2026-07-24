"""飞书"达人标签"的固定枚举生成（DATABASE_DEVELOPMENT.md §5.4）。

背景：``_creator_tags`` 原先从 AI 自由文本（creator_niche/content_focus）+ 硬编码
凑数标签（内容创作者/数字内容/品牌合作达人）拼接，结果散乱、每次不同、无统一范围。

本模块把达人标签改为**固定枚举 + 客观数据派生**：从 4 个维度（赛道/平台/粉丝量级/
语言）按优先级取 2–3 个，确保标签可控、稳定、有统一范围。

标签是运行时派生，不持久化（避免又一处冗余存储）。
"""
from __future__ import annotations

# 平台 → 规范标签（小写匹配）。
_PLATFORM_TAGS: dict[str, str] = {
    "youtube": "YouTube",
    "instagram": "Instagram",
    "tiktok": "TikTok",
    "x": "X",
    "twitter": "X",
}

# 语言码 → 博主标签。
_LANGUAGE_TAGS: dict[str, str] = {
    "en": "英语博主",
    "zh": "中文博主",
    "ja": "日语博主",
    "ko": "韩语博主",
}


def platform_tag(platform: str | None) -> str | None:
    """平台归一到规范标签（YouTube/Instagram/TikTok/X）。未知平台返回 None。"""
    if not platform:
        return None
    return _PLATFORM_TAGS.get(platform.strip().lower())


def follower_tier(followers: int | None) -> str | None:
    """粉丝量级 → 量级标签。

    - >=100万 → 百万粉
    - >=10万  → 十万粉
    - >=1万   → 万粉
    - >0      → 千粉
    - 0/None  → None（未知/无粉丝，不输出量级标签）
    """
    if not followers or followers <= 0:
        return None
    if followers >= 1_000_000:
        return "百万粉"
    if followers >= 100_000:
        return "十万粉"
    if followers >= 10_000:
        return "万粉"
    return "千粉"


def language_tag(language: str | None) -> str | None:
    """语言码 → 博主标签（仅常见语言映射，未知返回 None）。"""
    if not language:
        return None
    return _LANGUAGE_TAGS.get(language.strip().lower())


def build_creator_tags(
    niche: str | None,
    platform: str | None,
    followers: int | None,
    language: str | None,
) -> str:
    """生成 2–3 个达人标签，按优先级：赛道 → 平台 → 量级/语言。

    规则：
      1. 赛道（kol.niche，已归一为 18 个规范枚举）—— 优先级最高，必有。
      2. 平台（YouTube/Instagram/TikTok/X）。
      3. 第三位优先粉丝量级；若无粉丝数据但语言已知，用语言标签补位。

    返回 "、" 连接的字符串（如 "科技/AI、YouTube、十万粉"）。
    不足 2 个时返回已有的（至少赛道或平台之一）。
    """
    tags: list[str] = []
    seen_lower: set[str] = set()

    def _add(tag: str | None) -> None:
        if not tag:
            return
        if tag.lower() not in seen_lower:
            tags.append(tag)
            seen_lower.add(tag.lower())

    # 1. 赛道
    _add(niche)
    # 2. 平台
    _add(platform_tag(platform))
    # 3. 量级（优先）；无量级则尝试语言
    tier = follower_tier(followers)
    if tier:
        _add(tier)
    else:
        _add(language_tag(language))

    return "、".join(tags[:3])
