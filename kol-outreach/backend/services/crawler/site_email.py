"""官网深度邮箱采集。

YouTube about 页只有约 17% 的 KOL 直接留邮箱，但很多 KOL 在 about 里放官网链接，
官网的 /contact /about /press 页常有商务邮箱。本模块从 about 页抽出的官网 URL 出发，
小规模 BFS（每站点最多 N 页）抓取找邮箱，把邮箱率从 17% 提到 40%+。

移植自 youtube_collector/email_finder.py，精简掉 SSRF 全套（本系统跑在内网/受控环境，
目标域都是 KOL 公开官网），保留：
- 起始 URL 池
- 候选路径探测 (/contact, /about, /press, /business, /imprint, /kontakt ...)
- 每站点页数上限（礼貌限速）
- 邮箱提取复用 youtube.extract_emails（含混淆还原）
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse

from services.crawler import youtube

logger = logging.getLogger(__name__)

# 候选联系页面路径（多语言）。优先按这些路径探测官网。
CONTACT_PATHS = [
    "contact", "contact-us", "contactus",
    "about", "about-us", "aboutus",
    "business", "business-inquiries", "inquiries",
    "press", "media", "press-kit", "presskit",
    "booking", "work-with-me", "work-with-us", "services",
    "imprint", "legal",
    "kontakt",  # 德语
    "contato", "contacto",  # 葡/西语
]

# 网页里的 URL 链接抽取
_LinkRe = re.compile(r'href=["\']([^"\']+)["\']', re.I)


def _is_safe_host(host: str) -> bool:
    """主机名安全检查：排除 localhost/内网/无效。"""
    host = (host or "").lower().strip()
    if not host or "." not in host:
        return False
    if host in ("localhost", "127.0.0.1", "::1"):
        return False
    if host.startswith("192.168.") or host.startswith("10.") or host.startswith("172.16."):
        return False
    return True


# 社媒/平台域名（这些归 enrich.py 处理，深度采集不追）
_SOCIAL_HOSTS = (
    "instagram.com", "tiktok.com", "twitter.com", "x.com",
    "facebook.com", "youtube.com", "youtu.be", "reddit.com",
    "discord.gg", "discord.com", "t.me", "telegram.me",
    "linktr.ee", "beacons.ai", "tap.bio", "snipfeed.co",
    "itunes.apple.com", "play.google.com", "amazon.com",
    "patreon.com", "ko-fi.com", "buymeacoffee.com",
    "spotify.com", "soundcloud.com", "apple.com",
)


def _is_social_host(host: str) -> bool:
    host = (host or "").lower().strip()
    if host.startswith("www."):
        host = host[4:]
    return any(host == s or host.endswith("." + s) for s in _SOCIAL_HOSTS)


def _normalize_to_url(raw: str) -> str | None:
    """把 YouTube 外链块里的裸域/相对值规范化成完整 https URL。

    YouTube 的 channelExternalLinkViewModel 存的是 ``latashajames.com`` 而非
    ``https://latashajames.com``，且不含协议。这里补全协议并校验。
    返回 None 表示不是有效官网链接。
    """
    raw = (raw or "").strip().replace("\\/", "/")
    if not raw:
        return None
    # 去掉 \u0026 等转义
    raw = raw.replace("\\u0026", "&")
    # 已有协议
    if raw.startswith(("http://", "https://")):
        try:
            host = urlparse(raw).hostname
        except Exception:
            return None
    else:
        # 裸域：取首段作 host（去掉路径）
        host = raw.split("/")[0].split("?")[0]
        raw = f"https://{raw}"
    if not _is_safe_host(host):
        return None
    if _is_social_host(host):
        return None
    return raw


def extract_site_links_from_about(html: str) -> list[str]:
    """从 YouTube about 页 HTML 抽官网链接（非社媒的外链）。

    YouTube 把外链放在 channelExternalLinkViewModel 块里，存的是裸域
    （如 ``latashajames.com``）而非完整 URL。本函数补全协议、过滤社媒、
    返回完整 https URL 列表（每频道最多 3 个，避免爆炸）。
    """
    from services.crawler.enrich import _ExternalLinkRe  # 复用已有的外链正则

    candidates: list[str] = []
    for raw in _ExternalLinkRe.findall(html):
        url = _normalize_to_url(raw)
        if url:
            candidates.append(url)
    # 去重保序（按 host 去重）
    seen_hosts: set[str] = set()
    out: list[str] = []
    for u in candidates:
        host = urlparse(u).hostname.lower()
        if host not in seen_hosts:
            seen_hosts.add(host)
            out.append(u)
    return out[:3]


async def find_emails_on_site(fetcher, site_url: str, max_pages: int = 3) -> list[str]:
    """从一个官网 URL 出发，BFS 抓候选联系页面找邮箱。

    返回找到的所有邮箱（已小写去重，含混淆还原）。
    最多抓 max_pages 页（含首页），礼貌限速由 fetcher 保证。
    """
    found_emails: list[str] = []
    visited: set[str] = set()
    # 起始队列：官网首页 + 候选联系路径
    base = site_url.rstrip("/")
    queue: list[str] = [base]
    # 加候选路径（绝对 URL）
    parsed = urlparse(base)
    for path in CONTACT_PATHS:
        queue.append(f"{parsed.scheme}://{parsed.netloc}/{path}")

    while queue and len(visited) < max_pages:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            html = await fetcher.fetch_text(url)
        except Exception:
            continue
        if not html:
            continue
        # 抽邮箱
        emails = youtube.extract_emails(html)
        for e in emails:
            if e not in found_emails:
                found_emails.append(e)
        # 如果首页没找到邮箱，从首页抽更多内链继续 BFS
        if not found_emails and len(visited) < max_pages:
            for link in _LinkRe.findall(html)[:20]:
                abs_url = urljoin(url, link)
                try:
                    abs_host = urlparse(abs_url).hostname
                except Exception:
                    continue
                if not _is_safe_host(abs_host):
                    continue
                # 只追同域链接，避免跑偏
                if (abs_host or "").lower() != parsed.hostname.lower():
                    continue
                if abs_url not in visited and "contact" in abs_url.lower():
                    queue.append(abs_url)
        # 找到邮箱就停（够了）
        if found_emails:
            break
    return found_emails
