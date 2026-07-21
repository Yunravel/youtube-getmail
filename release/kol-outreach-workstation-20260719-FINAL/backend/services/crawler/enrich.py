"""多平台外链扩展。

平移自 Node ``kol_enrich_socials.mjs``：从一个 YouTube 频道的 about 页，
从简介与外链区块抽取 Instagram / TikTok / X 跨平台账号，扩展成多平台候选。

与 :mod:`youtube` 一样，本模块只做 HTML 解析，不做网络请求。
"""
from __future__ import annotations

import json
import re
from urllib.parse import unquote

from services.crawler.config_rules import socialHandleExcluded

# 频道简介 JSON 块
_DescriptionRe = re.compile(r'"description":"((?:\\.|[^"\\])*)","descriptionLabel"')

# 频道外链区块（YouTube channelExternalLinkViewModel）
_ExternalLinkRe = re.compile(
    r'"channelExternalLinkViewModel":\{"title":\{"content":"[^"]*"\},"link":\{"content":"([^"]+)"'
)

# 三平台 handle 提取
_IgRe = re.compile(r"(?:https?://)?(?:www\.)?instagram\.com/([A-Za-z0-9._]+)", re.I)
_TiktokRe = re.compile(r"(?:https?://)?(?:www\.)?tiktok\.com/@([A-Za-z0-9._-]+)", re.I)
_XRe = re.compile(r"(?:https?://)?(?:www\.)?(?:x|twitter)\.com/([A-Za-z0-9_]+)", re.I)


def _decoded_variants(html: str) -> list[str]:
    """对 HTML 做 2 轮 URI 解码 + 实体还原，返回去重变体列表。

    YouTube 把外链编码进 JS 字符串，单轮解码不够。对应 Node decodedVariants。
    """
    variants: list[str] = [html]
    current = html
    for _ in range(2):
        try:
            current = (
                current.replace("\\u0026", "&")
                .replace("\\/", "/")
                .replace("&amp;", "&")
            )
            # JS 的 decodeURIComponent 对纯文本多数是 no-op，但能还原 %XX
            decoded = unquote(current)
            if decoded not in variants:
                variants.append(decoded)
            current = decoded
        except Exception:
            break
    return variants


def _own_channel_text(html: str) -> str:
    """拼出频道自身可被外链抽取的文本：简介 + 外链 URL 列表。

    对应 Node ownChannelText。
    """
    parts: list[str] = []
    matches = _DescriptionRe.findall(html)
    if matches:
        raw = matches[-1]
        try:
            parts.append(json.loads(f'"{raw}"'))
        except (json.JSONDecodeError, ValueError):
            parts.append(raw)
    parts.extend(_ExternalLinkRe.findall(html))
    return "\n".join(parts)


def _normalize_handle(value: str) -> str:
    """去掉前导 @ 与路径/查询串，得到裸 handle。"""
    return re.sub(r"^@|[/?#].*$", "", value).strip()


def _profile_url(platform: str, handle: str) -> str:
    if platform == "Instagram":
        return f"https://www.instagram.com/{handle}/"
    if platform == "TikTok":
        return f"https://www.tiktok.com/@{handle}"
    if platform == "X":
        return f"https://x.com/{handle}"
    return f"https://www.youtube.com/@{handle}"


def extract_profiles(html: str) -> list[dict]:
    """从频道 about 页 HTML 抽取跨平台社媒账号。

    返回 ``[{"platform", "handle", "profile_url"}, ...]``，按 "平台:小写handle" 去重。
    排除非用户名的路径段（accounts/explore/reel 等）。
    """
    profiles: dict[str, dict] = {}

    def _add(platform: str, handle: str) -> None:
        cleaned = _normalize_handle(handle)
        if not cleaned or len(cleaned) > 64:
            return
        if cleaned.lower() in socialHandleExcluded:
            return
        key = f"{platform}:{cleaned.lower()}"
        if key not in profiles:
            profiles[key] = {
                "platform": platform,
                "handle": cleaned,
                "profile_url": _profile_url(platform, cleaned),
            }

    text_block = _own_channel_text(html)
    for variant in _decoded_variants(text_block):
        for m in _IgRe.findall(variant):
            _add("Instagram", m)
        for m in _TiktokRe.findall(variant):
            _add("TikTok", m)
        for m in _XRe.findall(variant):
            _add("X", m)
    return list(profiles.values())
