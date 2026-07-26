"""飞书“达人标签”的固定枚举生成（DATABASE_DEVELOPMENT.md §5.4）。

背景：``_creator_tags`` 原先从 AI 自由文本（creator_niche/content_focus）+ 硬编码
凑数标签（内容创作者/数字内容/品牌合作达人）拼接，结果散乱、每次不同、无统一范围。

本模块把达人标签改为**固定枚举 + 客观数据派生**：从 3 个维度（赛道/平台/粉丝量级）
按优先级取 2–3 个，确保标签可控、稳定、有统一范围。

标签是运行时派生，不持久化（避免又一处冗余存储）。
"""
from __future__ import annotations

# 内容赛道白名单。任何 AI 自由文本、产品名或未识别文本都不能直接进入飞书。
CONTENT_TAGS: tuple[str, ...] = (
    "科技/AI",
    "游戏",
    "美妆",
    "时尚",
    "健身",
    "美食",
    "旅游",
    "教育",
    "搞笑",
    "生活方式",
    "音乐",
    "商业财经",
    "育儿",
    "健康",
    "艺术/设计",
    "汽车",
    "体育",
    "UGC创作",
    "其他",
)

# 平台 → 规范标签（小写匹配）。
_PLATFORM_TAGS: dict[str, str] = {
    "youtube": "YouTube",
    "instagram": "Instagram",
    "tiktok": "TikTok",
    "x": "X",
    "twitter": "X",
}

PLATFORM_TAGS = tuple(dict.fromkeys(_PLATFORM_TAGS.values()))
FOLLOWER_TAGS = ("千粉", "万粉", "十万粉", "百万粉")
ALLOWED_CREATOR_TAGS = frozenset(
    CONTENT_TAGS + PLATFORM_TAGS + FOLLOWER_TAGS
)


def content_tag(niche: str | None) -> str:
    """把赛道限制在固定白名单；未知值统一为“其他”。

    这里故意不保留原始文本。原始 niche 仍留在数据库用于审计，但飞书标签必须稳定，
    因此 Dreamina/Kimi/Pippit、AI 自由描述和脏值都只能落到“其他”。
    """
    value = (niche or "").strip()
    return value if value in CONTENT_TAGS else "其他"


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


def build_creator_tags(
    niche: str | None,
    platform: str | None,
    followers: int | None,
    language: str | None,
) -> str:
    """生成 2–3 个达人标签，按优先级：赛道 → 平台 → 量级/语言。

    规则：
      1. 赛道（固定枚举，未知统一为“其他”）—— 优先级最高，必有。
      2. 平台（YouTube/Instagram/TikTok/X）。
      3. 粉丝量级。

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

    # 1. 赛道。严禁把 AI 自由文本或产品名原样写入飞书。
    _add(content_tag(niche))
    # 2. 平台
    _add(platform_tag(platform))
    # 3. 量级。language 参数仅为兼容既有调用签名，不再生成语言标签。
    _add(follower_tier(followers))

    result = tags[:3]
    # 防御性校验：以后即使有人改了上游映射，也不能绕过飞书标签白名单。
    if any(tag not in ALLOWED_CREATOR_TAGS for tag in result):
        raise ValueError(f"达人标签超出固定枚举: {result}")
    return "、".join(result)
