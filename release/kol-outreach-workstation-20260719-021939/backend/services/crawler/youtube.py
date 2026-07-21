"""YouTube 搜索结果与 about 页解析。

平移自 Node ``kol_collect.mjs`` 的纯函数（handlesFromSearch / metaContent /
fullChannelDescription / extractEmails / explicitCountry / inferredCountry /
businessSignal / channelType）。这些函数只做 HTML 文本解析，不做网络请求；
网络由 :mod:`fetcher` 负责。这样解析逻辑可独立单测。

YouTube 页面结构会变。所有正则集中在本文件，便于改版时一次性修复。
"""
from __future__ import annotations

import json
import re
from typing import Any

from services.crawler.config_rules import (
    badEmailFragments,
    businessSignalRe,
    countryAliases,
)

# 搜索结果页里 canonicalBaseUrl 字段，形如 "canonicalBaseUrl":"/@handle"
_HandleRe = re.compile(r'"canonicalBaseUrl":"(/@[^"]+)"')

# 频道 country 声明字段
_CountryRe = re.compile(r'"country":"([^"]+)"')

# 频道完整简介：description 与 descriptionLabel 之间的大段文本
_DescriptionRe = re.compile(r'"description":"((?:\\.|[^"\\])*)","descriptionLabel"')

# 通用字段提取（如 subscriberCountText）
_EmailPattern = re.compile(
    r"[A-Z0-9][A-Z0-9._%+-]*@[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?"
    r"(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+",
    re.I,
)

# 混淆邮箱写法：KOL 常用 roboverse (at) youripartnerships (dot) com 防爬。
# 覆盖两种形态：
#   严格：xxx (at) yyy (dot) com   —— 域名段全用占位符
#   半严格：xxx (at) yyy.com        —— 只有 (at)，域名用真实点号
# 移植自 youtube_collector/email_finder.py 并放宽。
_ObfuscatedRe = re.compile(
    r"(?i)([\w.+-]+)\s*(?:\[at\]|\(at\))\s*"
    r"([\w-]+(?:\s*(?:\[dot\]|\(dot\))\s*[\w-]+)*"
    r"(?:\.[\w-]+)*)"
)
_DotPlaceholderRe = re.compile(r"\s*(?:\[dot\]|\(dot\))\s*", re.I)


def _decode_html(value: str) -> str:
    """还原 HTML 实体与 JS 转义。对应 Node decodeHtml。"""
    return (
        value.replace("&amp;", "&")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("\\u0026", "&")
        .replace("\\u0027", "'")
        .replace("\\n", "\n")
        .replace("\\/", "/")
    )


def handles_from_search(html: str) -> list[str]:
    """从 YouTube 搜索结果页提取频道 handle（去重保序）。"""
    seen: list[str] = []
    for m in _HandleRe.findall(html):
        if m not in seen:
            seen.append(m)
    return seen


def meta_content(html: str, name: str) -> str:
    """提取 <meta name="X" content="Y"> 的 Y。两种属性顺序都兼容。"""
    for pattern in (
        re.compile(rf'<meta[^>]+name="{re.escape(name)}"[^>]+content="([^"]*)"', re.I),
        re.compile(rf'<meta[^>]+content="([^"]*)"[^>]+name="{re.escape(name)}"', re.I),
    ):
        m = pattern.search(html)
        if m:
            return _decode_html(m.group(1))
    return ""


def full_channel_description(html: str) -> str:
    """提取频道完整简介文本。优先 JSON description 字段，退回 meta description。"""
    matches = _DescriptionRe.findall(html)
    if matches:
        raw = matches[-1]
        try:
            # 用 JSON 解码还原转义（与 Node 一致）
            return _decode_html(json.loads(f'"{raw}"'))
        except (json.JSONDecodeError, ValueError):
            return _decode_html(raw)
    return meta_content(html, "description")


def field_from_html(html: str, field: str) -> str:
    """提取形如 "subscriberCountText":"123 subscribers" 的字段值（取最后一个匹配）。"""
    matches = re.findall(rf'"{re.escape(field)}":"([^"]+)"', html)
    return _decode_html(matches[-1]) if matches else ""


def extract_emails(text: str) -> list[str]:
    """从文本提取邮箱。小写、去重、过滤黑名单与超长/连续点。

    先还原 KOL 常用的混淆写法（``roboverse (at) ypi (dot) com`` → 标准邮箱），
    再用标准正则提取。对应 Node extractEmails + email_finder.py 的反混淆。
    长度上限 254（RFC 5321）。
    """
    if not text:
        return []
    # 还原混淆写法：把 xxx (at) yyy (dot) com 拼成标准邮箱追加到原文末尾
    decoded = text
    for m in _ObfuscatedRe.finditer(text):
        domain = _DotPlaceholderRe.sub(".", m.group(2))
        decoded += f" {m.group(1)}@{domain}"

    seen: list[str] = []
    for m in _EmailPattern.findall(decoded):
        email = m.lower()
        if (
            len(email) <= 254
            and ".." not in email
            and not any(frag in email for frag in badEmailFragments)
            and email not in seen
        ):
            seen.append(email)
    return seen


def explicit_country(html: str) -> str:
    """频道平台声明的国家（"country"字段）。取最后一个。"""
    matches = _CountryRe.findall(html)
    return _decode_html(matches[-1]) if matches else ""


def inferred_country(description: str, title: str) -> str:
    """从标题+简介推断国家。按 countryAliases 顺序匹配，首个命中即返回。"""
    haystack = f"{title}\n{description}"
    for country, pattern in countryAliases:
        if pattern.search(haystack):
            return country
    return ""


def business_signal(description: str, email: str) -> bool:
    """邮箱附近 100 字符内是否含商务关键词。对应 Node businessSignal。"""
    at = description.lower().find(email.lower())
    if at >= 0:
        context = description[max(0, at - 100): at + len(email) + 100]
    else:
        context = description
    return bool(businessSignalRe.search(context))


def channel_type(title: str, description: str) -> str:
    """频道类型粗分。对应 Node channelType。"""
    text = f"{title} {description}".lower()
    if re.search(r"\b(official|corporation|company|university|school|institute|news network)\b", text):
        return "可能为机构/官方"
    if re.search(r"\b(podcast|newsletter|media)\b", text):
        return "媒体/播客"
    return "个人创作者/工作室"
