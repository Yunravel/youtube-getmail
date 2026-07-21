"""Instagram / TikTok / X 主页解析器（纯函数）。

与 :mod:`services.crawler.youtube` 同风格：只做 HTML/JSON 文本解析，不发网络
请求。用于 :mod:`scripts.backfill_kol_fields` 对已有 KOL 的粉丝数 / 国家 / 赛道
字段补全。

设计要点
--------
- 各平台页面结构经常变。所有正则与 JSON 取值路径集中在本文件，改版时只改这里。
- **不依赖** 任何平台的官方 API，全部基于公开页面的 meta 标签 / 内嵌 JSON。
- 反爬重定向到登录页（典型：IG ``/accounts/login/``、TikTok ``login``、X ``x.com/i/flow/login``）
  统一识别并返回 ``None``，由调用方进 missed.csv。
- 统一返回 :class:`ProfileParse`，调用方只面向这一个类型。

成功拿到 ``followers`` 是首要目标；country / niche_hint 是尽力而为（None 不报错）。
"""
from __future__ import annotations

import json
import re
from typing import Optional

from pydantic import BaseModel


class ProfileParse(BaseModel):
    """跨平台统一的解析结果。所有字段尽力而为，None 表示未取到。"""

    followers: Optional[int] = None
    country: Optional[str] = None
    niche_hint: Optional[str] = None  # 关键词规则推断的赛道，如 tech / beauty
    title: Optional[str] = None       # 账号显示名 / meta title
    description: Optional[str] = None  # 简介 / meta description
    source: str = ""                  # 命中的解析路径，便于排查（如 og / sigi / html）


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

# og:description / og:title meta 提取（两种属性顺序都兼容）
def _meta(html: str, name: str) -> str:
    """提取 <meta property="X" content="Y"> 或 name= 形式的 Y。"""
    for pattern in (
        re.compile(
            rf'<meta[^>]+(?:property|name)=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']*)',
            re.I,
        ),
        re.compile(
            rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']{re.escape(name)}["\']',
            re.I,
        ),
    ):
        m = pattern.search(html)
        if m:
            return _decode(m.group(1))
    return ""


def _decode(value: str) -> str:
    return (
        value.replace("&amp;", "&")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("\\u0026", "&")
        .replace("\\/", "/")
        .strip()
    )


def _parse_count(text: str) -> Optional[int]:
    """解析 '1.2M Followers' / '12,345' / '1,234,567 Followers' 这类文本。

    支持 K/M/B 后缀（不区分大小写）。失败返回 None。
    """
    if not text:
        return None
    s = text.lower().replace(",", "").strip()
    # 找第一个「数字 + 可选后缀」片段
    m = re.search(r"([\d.]+)\s*([kmb]?)", s)
    if not m:
        return None
    num_str, suffix = m.group(1), m.group(2)
    mult = {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[suffix]
    try:
        value = float(num_str) * mult
    except ValueError:
        return None
    return int(value) if value >= 1 else None


# 关键词 → 赛道（与业务上 niche 用法对齐：英文小写词）。
# 与 services.ai_personalize.analyze_niche 输出风格一致（tech / beauty / gaming …），
# 但本表是纯规则，**不调 LLM**，省 token。
_NICHE_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("tech", ("tech", "ai", "gadget", "software", "coding", "programming", "apps", "review")),
    ("gaming", ("gaming", "gamer", "gameplay", "twitch", "esports", "streamer")),
    ("beauty", ("beauty", "makeup", "skincare", "cosmetics", "mua")),
    ("fashion", ("fashion", "style", "outfit", "ootd", "model")),
    ("fitness", ("fitness", "workout", "gym", "bodybuilding", "yoga", "crossfit")),
    ("food", ("food", "recipe", "cooking", "baking", "chef", "foodie")),
    ("travel", ("travel", "wanderlust", "nomad", "adventure", "explore")),
    ("education", ("education", "tutorial", "learn", "study", "teach", "science")),
    ("comedy", ("comedy", "funny", "humor", "meme", "prank")),
    ("lifestyle", ("lifestyle", "vlog", "daily", "life")),
    ("music", ("music", "musician", "singer", "songwriter", "producer", "dj")),
    ("business", ("business", "entrepreneur", "marketing", "startup", "finance", "money")),
    ("parenting", ("parenting", "mom", "dad", "family", "baby", "kids")),
    ("health", ("health", "wellness", "mental", "nutrition", "diet")),
    ("art", ("art", "artist", "illustration", "design", "creative")),
    ("auto", ("car", "auto", "vehicle", "motorcycle", "motor")),
    ("sports", ("sport", "football", "basketball", "soccer", "baseball", "tennis")),
]


def _infer_niche(*texts: str) -> Optional[str]:
    """从给定的若干段文本（title/description）按关键词命中推断赛道。

    取首个命中的关键词类（按 _NICHE_KEYWORDS 顺序），未命中返回 None。
    """
    haystack = " ".join(t for t in texts if t).lower()
    if not haystack:
        return None
    for niche, keywords in _NICHE_KEYWORDS:
        for kw in keywords:
            # 词边界匹配，避免 'mua' 命中 'amual'
            if re.search(rf"\b{re.escape(kw)}\b", haystack):
                return niche
    return None


# 各平台反爬重定向的登录页标识（命中即视为抓取失败）
_LOGIN_HINTS = (
    "accounts/login",
    "/login",
    "i/flow/login",
    "signup",
    "Please log in",
    "您需要登录",
    "请登录",
    "Please sign in",
)


def _is_login_wall(html_or_text: str) -> bool:
    """粗判页面是否被反爬重定向到了登录页。"""
    if not html_or_text:
        return True
    head = html_or_text[:4000].lower()
    return any(hint.lower() in head for hint in _LOGIN_HINTS) and len(html_or_text) < 8000


# ---------------------------------------------------------------------------
# Instagram
# ---------------------------------------------------------------------------

# og:description 形如 "1,234 Followers, 567 Following, 89 Posts - See Instagram
# photos and videos from Handle (@handle)"
_IG_FOLLOWERS_RE = re.compile(
    r"([\d.,]+[kmbKMB]?)\s*[,]?\s*[Ff]ollowers"
)


def parse_instagram(html: str) -> Optional[ProfileParse]:
    """解析 Instagram 主页 HTML。

    优先 og:description（公开 meta，反爬最弱），失败时尝试内嵌 JSON。

    返回 ``None`` 表示被反爬登录墙拦截（调用方进 missed.csv）。
    """
    if not html or _is_login_wall(html):
        return None

    title = _meta(html, "og:title")
    description = _meta(html, "og:description")

    # followers：og:description 优先
    followers = _parse_count(description) if description else None
    source_tag = "og"

    # 兜底：内嵌 JSON（取 text_followers / edge_followed_by 等已知字段）
    if followers is None:
        for pattern in (
            re.compile(r'"edge_followed_by":\s*\{\s*"count":\s*(\d+)'),
            re.compile(r'"text_followers":\s*"([^"]+)"'),
            re.compile(r'"follower_count":\s*(\d+)'),
        ):
            m = pattern.search(html)
            if m:
                followers = _parse_count(m.group(1))
                if followers is not None:
                    source_tag = "json"
                    break

    # 都没拿到：要么是登录墙截断，要么是页面结构变了
    if followers is None:
        return None

    niche_hint = _infer_niche(title, description)
    # IG 主页一般不暴露国家（公开 og 不含），不强行造
    return ProfileParse(
        followers=followers,
        country=None,
        niche_hint=niche_hint,
        title=title or None,
        description=description or None,
        source=source_tag,
    )


# ---------------------------------------------------------------------------
# TikTok
# ---------------------------------------------------------------------------

def parse_tiktok(html: str) -> Optional[ProfileParse]:
    """解析 TikTok 主页 HTML。

    优先从 ``__UNIVERSAL_DATA_FOR_REHYDRATION__`` / 旧版 SIGI_STATE 内嵌 JSON
    取 follower_count；退回 og:description。

    返回 ``None`` 表示被反爬登录墙拦截或没拿到关键字段。
    """
    if not html or _is_login_wall(html):
        return None

    title = _meta(html, "og:title")
    description = _meta(html, "og:description")

    followers: Optional[int] = None
    country: Optional[str] = None
    niche_hint: Optional[int] = None
    source_tag = ""

    # 内嵌 JSON：取各种已知结构的 follower_count
    json_patterns = (
        # 新版 Universal 数据
        re.compile(
            r'<script[^>]*id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
            re.DOTALL,
        ),
        # 旧版 SIGI_STATE
        re.compile(r'<script[^>]*>window\._{0,2}SIGI_STATE_{0,2}\s*=\s*(\{.*?\});</script>', re.DOTALL),
        re.compile(r'<script[^>]*id="SIGI_STATE"[^>]*>(.*?)</script>', re.DOTALL),
    )

    blob = ""
    for pat in json_patterns:
        m = pat.search(html)
        if m:
            blob = m.group(1)
            break

    if blob:
        try:
            data = json.loads(blob)
        except (json.JSONDecodeError, ValueError):
            data = None
        if isinstance(data, dict):
            # TikTok 把数据塞在不同路径下，逐一试（覆盖新旧版本）。
            # 实测 2026-07 真实路径：__DEFAULT_SCOPE__.webapp.user-detail.userInfo.stats.followerCount
            followers_candidates = (
                _walk(data, ("__DEFAULT_SCOPE__", "webapp.user-detail", "userInfo", "stats", "followerCount")),
                _walk(data, ("__DEFAULT_SCOPE__", "webapp.user-detail", "userInfo", "statsV2", "followerCount")),
                _walk(data, ("__DEFAULT_SCOPE__", "webapp.user-detail", "userInfo", "user", "followerCount")),
                _walk(data, ("UserModule", "users", "followerCount")),
                _walk(data, ("userInfo", "user", "followerCount")),
                _walk(data, ("userInfo", "stats", "followerCount")),
                _walk(data, ("user", "followerCount")),
            )
            for v in followers_candidates:
                if isinstance(v, (int, float)) and v > 0:
                    followers = int(v)
                    source_tag = "json"
                    break
            # 国家：TikTok 有时会给 region / location
            region = (
                _walk(data, ("__DEFAULT_SCOPE__", "webapp.user-detail", "userInfo", "user", "region"))
                or _walk(data, ("UserModule", "users", "region"))
            )
            if isinstance(region, str) and region.strip():
                country = region.strip().upper()
            # niche：从 signature / bio 推断
            sig = (
                _walk(data, ("__DEFAULT_SCOPE__", "webapp.user-detail", "userInfo", "user", "signature"))
                or _walk(data, ("UserModule", "users", "signature"))
            )
            niche_hint = _infer_niche(title or "", description or "", sig or "")

    # 兜底：og:description（"123 Followers. 45 Following. 67 Likes. ..."）
    if followers is None and description:
        m = _IG_FOLLOWERS_RE.search(description)
        if m:
            followers = _parse_count(m.group(1))
            if followers is not None:
                source_tag = "og"

    if followers is None:
        return None

    if niche_hint is None:
        niche_hint = _infer_niche(title, description)

    return ProfileParse(
        followers=followers,
        country=country,
        niche_hint=niche_hint,
        title=title or None,
        description=description or None,
        source=source_tag or "og",
    )


# ---------------------------------------------------------------------------
# X (Twitter)
# ---------------------------------------------------------------------------

# og:description 形如 "12345 Followers. 678 Following. ... "
_X_FOLLOWERS_RE = re.compile(r"([\d.,]+[kmbKMB]?)\s*[Ff]ollowers")


def parse_x(html: str) -> Optional[ProfileParse]:
    """解析 X / Twitter 主页 HTML。

    X 在未登录状态下会跳到登录墙，``og:description`` **只含 bio 文本，不含
    followers 数字**。所以本函数常常返回 ``None``（让调用方进 missed.csv）——
    这是 X 的硬反爬，不是解析器 bug。要拿到 followers 必须登录态或调官方 API。

    若 og:title/description 能取到，至少把 niche_hint / description 填上，
    但因 ``ProfileParse.followers`` 为 None，调用方默认视为失败。
    """
    if not html or _is_login_wall(html):
        return None

    title = _meta(html, "og:title")
    description = _meta(html, "og:description")

    # og:description 里偶尔会有 "1.2K Followers" 文本（嵌入 widget 渲染过的），
    # 真实情况基本拿不到，但还是试一下
    followers = None
    if description:
        m = _X_FOLLOWERS_RE.search(description)
        if m:
            followers = _parse_count(m.group(1))

    if followers is None:
        # X 反爬：未登录拿不到 followers。返回 None 让调用方进 missed.csv。
        return None

    niche_hint = _infer_niche(title, description)
    return ProfileParse(
        followers=followers,
        country=None,
        niche_hint=niche_hint,
        title=title or None,
        description=description or None,
        source="og",
    )


# ---------------------------------------------------------------------------
# 工具：在嵌套 dict / list 里按路径取值
# ---------------------------------------------------------------------------

def _walk(obj, path: tuple) -> Optional[object]:
    """按路径在嵌套 dict/list 中取值。任一节点缺失返回 None。"""
    cur: object = obj
    for key in path:
        if isinstance(cur, dict):
            cur = cur.get(key)
        elif isinstance(cur, list) and isinstance(key, int) and 0 <= key < len(cur):
            cur = cur[key]
        else:
            return None
        if cur is None:
            return None
    return cur


# ---------------------------------------------------------------------------
# 路由器：按平台名分发
# ---------------------------------------------------------------------------

_DISPATCH = {
    "instagram": parse_instagram,
    "tiktok": parse_tiktok,
    "x": parse_x,
}


def parse_profile(platform: str, html: str) -> Optional[ProfileParse]:
    """按平台名分发到对应解析器。

    平台名归一化（小写），未知平台返回 None。
    YouTube 不在本路由器内 —— YouTube 走 services.crawler.youtube（结构稳定）。
    """
    fn = _DISPATCH.get((platform or "").strip().lower())
    if fn is None:
        return None
    return fn(html)
