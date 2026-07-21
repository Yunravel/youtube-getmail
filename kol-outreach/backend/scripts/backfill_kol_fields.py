"""KOL 字段补全爬取：从公开主页回填 followers / country / niche / position。

目标：对已有联系邮箱但资料不全的 KOL，按 HotLead 报价单列补全粉丝数、国家、
社会身份/内容定位。覆盖 YouTube / Instagram / TikTok / X 四个平台。

为什么单独写而不复用 ``services.crawler.pipeline``
-----------------------------------------------------
``pipeline.run_crawl`` 是「关键词发现 → 频道」的正向流水线，不处理「已有 KOL →
补字段」的反向场景。它也从不填 ``avg_views`` 与 ``recent_videos``（参见
``profile_parsers`` 模块文档）。本脚本是对着已有 KOL 行反向爬，所以是新的入口。

复用与新增
----------
- **复用**：``HttpxFetcher``（YouTube about 页 httpx 足够）、
  ``youtube.py`` + ``normalize.resolve_country`` + ``normalize.parse_subscriber_count``
  （YouTube 解析已成熟）。
- **新增**：``services.crawler.profile_parsers``（IG/TT/X 解析器，纯函数）。
- **直接调 Playwright**（不走 ``PlaywrightFetcher.fetch_text``）：后者返回
  ``body.inner_text()``，会丢掉 meta 标签与内嵌 JSON，而 IG/TT/X 解析器依赖的
  恰恰是 og:description / SIGI_STATE / UNIVERSAL_DATA。本脚本里用一个轻量封装
  ``_PwHtmlFetcher`` 直接拿 ``page.content()``。

幂等：默认只填空字段（``--force`` 才覆盖）。
分批：失败/反爬的行写 ``backfill_missed_{batch}.csv``，不阻塞主流程。

CLI 用法
--------
  # YouTube 小样本预览（不写库）
  python -m scripts.backfill_kol_fields --platform youtube --limit 5

  # YouTube 写库
  python -m scripts.backfill_kol_fields --platform youtube --commit

  # Instagram 写库（需要本地装了 chromium）
  python -m scripts.backfill_kol_fields --platform instagram --commit

  # 全平台写库
  python -m scripts.backfill_kol_fields --commit
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

# Windows 下默认 Proactor 事件循环在关闭时会刷一堆 ResourceWarning 噪音
# （"I/O operation on closed pipe"）。SelectorLoop 没这问题，但 Playwright
# 文档要求用 Proactor。这里只在 module load 时抑制这些噪音，事件循环策略不动。
if sys.platform == "win32":
    import warnings as _warnings
    _warnings.filterwarnings("ignore", category=ResourceWarning)

from sqlalchemy.orm import Session

from config import settings
from db import SessionLocal
from models import Kol
from services.crawler import normalize, youtube
from services.crawler.fetcher import HttpxFetcher, gather_pool
from services.crawler.profile_parsers import ProfileParse, parse_profile

logger = logging.getLogger(__name__)

# 平台归一化映射：DB 里有 YouTube/youtube 等大小写不一致。
_PLATFORM_NORMALIZE = {
    "youtube": "YouTube",
    "yt": "YouTube",
    "instagram": "Instagram",
    "ig": "Instagram",
    "tiktok": "TikTok",
    "tik tok": "TikTok",
    "x": "X",
    "twitter": "X",
    "twitter/x": "X",
}

# 需要补全的字段名（HotLead 报价单相关 + 影响筛选的 country）
_TARGET_FIELDS = ("followers", "country", "niche", "position")


# ---------------------------------------------------------------------------
# 轻量 Playwright HTML 抓取器（取 page.content() 而非 inner_text）
# ---------------------------------------------------------------------------


class _PwHtmlFetcher:
    """直接用 Playwright 抓 raw HTML（含 meta / JSON script）。

    与 ``services.crawler.fetcher.PlaywrightFetcher`` 区别：后者面向「深度邮箱」
    返回 body 文本；本类返回整页 HTML 供 meta / JSON 解析。每个实例起一个浏览器，
    抓完调用方负责 ``aclose``。
    """

    def __init__(self, *, timeout: int = 20, user_agent: Optional[str] = None):
        self._timeout = timeout
        self._ua = user_agent or getattr(
            settings,
            "CRAWLER_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/138 Safari/537.36",
        )
        self._playwright = None
        self._browser = None
        self._context = None

    async def _ensure(self):
        if self._browser is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "IG/TikTok/X 补全需要 playwright：\n"
                "  pip install playwright\n"
                "  playwright install chromium"
            ) from e
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        self._context = await self._browser.new_context(user_agent=self._ua)

    async def fetch_html(self, url: str) -> str:
        """抓页面整页 HTML。失败返回 ''。"""
        await self._ensure()
        page = await self._context.new_page()
        try:
            resp = await page.goto(
                url, wait_until="domcontentloaded", timeout=self._timeout * 1000
            )
            if resp is None or resp.status >= 400:
                logger.debug("pw fetch %s -> %s", url, resp.status if resp else "none")
                return ""
            # 给 SPA 留点渲染时间
            await page.wait_for_timeout(2500)
            return await page.content()
        except Exception as e:
            logger.debug("pw fetch %s error: %s", url, e)
            return ""
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def aclose(self):
        for closer in (self._context, self._browser, self._playwright):
            if closer is not None:
                try:
                    await closer.close()
                except Exception:
                    pass
        self._context = self._browser = self._playwright = None


# ---------------------------------------------------------------------------
# 平台路由：每个平台一个 (取URL, 抓取器, 解析) 组合
# ---------------------------------------------------------------------------


def _youtube_about_url(kol: Kol) -> Optional[str]:
    """YouTube KOL 的 about URL。优先 channel_url，其次 profile_url。"""
    base = kol.channel_url or kol.profile_url or ""
    base = base.strip()
    if not base:
        return None
    # 已经是 about 页就直接用
    if base.endswith("/about"):
        return base
    return base.rstrip("/") + "/about"


# ---------------------------------------------------------------------------
# 邮箱域反查：从 email@domain → 抓网站首页 → 抽 platform/handle/profile_url
# ---------------------------------------------------------------------------

# 邮箱域黑名单：通用邮箱服务商，没有个人网站可反查
_FREE_EMAIL_DOMAINS = {
    "gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com",
    "live.com", "msn.com", "aol.com", "proton.me", "protonmail.com",
    "me.com", "mac.com", "gmx.com", "gmx.net", "yandex.com", "yandex.ru",
    "mail.com", "zoho.com", "tutanota.com",
    # gmail 变体（DB 里见过 gmail.com.watch 这种垃圾值）
    "gmail.com.watch",
}

# 通用网站首页上常见的非用户名路径段
_SOCIAL_HANDLE_EXCLUDED = {
    "accounts", "explore", "reel", "reels", "hashtag", "home", "i", "intent",
    "search", "share", "channel", "c", "p", "tos", "privacy", "embed",
    "login", "signup", "register", "watch", "v", "shorts", "feed",
}

# 社媒平台链接模式：(platform 显示名, URL pattern, profile_url 构造器)
# 注意排序：YouTube 优先于其他（很多网站都链到自己的 YT）
#
# YouTube 有两种 URL 形式：
#   - @handle（推荐，如 youtube.com/@ExposureNinja）
#   - /channel/UCxxxx（旧版 channel ID，UC 开头 24 位）
# 抽到 channel ID 时不能当 @handle 拼回 URL（会变成无效的 /@UCxxx），
# 这里用两个独立模式分别处理。
_SOCIAL_PATTERNS: list[tuple[str, "re.Pattern", callable]] = [
    # YouTube @handle（优先）
    ("YouTube", re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/(?:@|c/|user/)([A-Za-z0-9._-]+)"),
     lambda h: f"https://www.youtube.com/@{h}"),
    # YouTube /channel/UCxxxx（保留原 URL，handle 用 channel ID 但拼成 /channel/ 形式）
    ("YouTube", re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/channel/(UC[A-Za-z0-9_-]{20,26})"),
     lambda h: f"https://www.youtube.com/channel/{h}"),
    ("Instagram", re.compile(r"(?:https?://)?(?:www\.)?instagram\.com/([A-Za-z0-9._]+)"),
     lambda h: f"https://www.instagram.com/{h}/"),
    ("TikTok", re.compile(r"(?:https?://)?(?:www\.)?tiktok\.com/@([A-Za-z0-9._-]+)"),
     lambda h: f"https://www.tiktok.com/@{h}"),
    ("X", re.compile(r"(?:https?://)?(?:www\.)?(?:x|twitter)\.com/([A-Za-z0-9_]+)"),
     lambda h: f"https://x.com/{h}"),
]


def _email_domain(email: str) -> Optional[str]:
    """从邮箱取域；通用邮箱 / 无效邮箱返回 None。"""
    if not email or "@" not in email:
        return None
    domain = email.rsplit("@", 1)[-1].strip().lower()
    if not domain or "." not in domain:
        return None
    if domain in _FREE_EMAIL_DOMAINS:
        return None
    return domain


def _extract_socials_from_html(html: str) -> list[dict]:
    """从任意网站首页 HTML 抽 IG/TT/X/YT 链接。

    与 :func:`services.crawler.enrich.extract_profiles` 区别：enrich 依赖
    YouTube 特有的 JSON 结构（channelExternalLinkViewModel），本函数直接扫
    全文（适配任意个人网站）。返回 ``[{"platform","handle","profile_url"}]``。
    """
    out: dict[str, dict] = {}
    for platform, pattern, url_builder in _SOCIAL_PATTERNS:
        for raw in pattern.findall(html):
            handle = re.sub(r"^@|[/?#].*$", "", raw).strip()
            if not handle or len(handle) > 64:
                continue
            if handle.lower() in _SOCIAL_HANDLE_EXCLUDED:
                continue
            key = f"{platform}:{handle.lower()}"
            if key not in out:
                out[key] = {
                    "platform": platform,
                    "handle": handle,
                    "profile_url": url_builder(handle),
                }
    return list(out.values())


def _apply_socials_to_kol(
    kol: Kol, socials: list[dict], *, force: bool
) -> dict:
    """把抽到的社交账号填入 KOL（仅填空）。返回字段变更明细。

    优先级：
      - platform/handle/profile_url 三件套一起填，优先取抽到的第一个（按
        _SOCIAL_PATTERNS 顺序：YT > IG > TT > X）。
      - company_site 不在此处填 —— 由 :func:`_process_domain_lookup` 用实际
        抓到内容的 URL（``used_url``）填，避免用邮箱子域（如 support.xxx.com）。
    """
    changes: dict = {}
    if not socials:
        return changes

    # 选第一个 social 作为 platform/handle/profile_url 的来源
    first = socials[0]
    norm_plat = _PLATFORM_NORMALIZE.get(first["platform"].lower(), first["platform"])
    # platform 覆盖条件：原值为空/占位符，或原值未规范化（小写如 'youtube'，通常
    # 是 Snov 拍脑袋填的）。规范化的平台名（如 'Instagram' 来自原 KOL-Find 导入）
    # 视为可信，不覆盖 —— 即使网站首页抽到的是别的平台（博主首页常链多个平台）。
    cur_plat_norm = _PLATFORM_NORMALIZE.get((kol.platform or "").lower(), kol.platform or "")
    platform_untrusted = (
        not kol.platform
        or kol.platform != cur_plat_norm          # 小写/未规范化（'youtube' 而非 'YouTube'）
        or cur_plat_norm in ("", "Other")
    )
    if force or platform_untrusted:
        changes["platform"] = {"from": kol.platform, "to": norm_plat}
        kol.platform = norm_plat
    if force or _is_empty(kol.social_handle):
        # 去掉前导 @，保持与 DB 中其它行一致（部分行有 @ 部分没有，但多数是裸 handle）
        handle = first["handle"].lstrip("@")
        changes["social_handle"] = {"from": kol.social_handle, "to": handle}
        kol.social_handle = handle
    if force or (_is_empty(kol.profile_url) and _is_empty(kol.channel_url)):
        changes["profile_url"] = {"from": kol.profile_url, "to": first["profile_url"]}
        kol.profile_url = first["profile_url"]
        # channel_url 只在 YouTube 时填
        if norm_plat == "YouTube":
            kol.channel_url = first["profile_url"]
    return changes


async def _process_domain_lookup(
    http_fetcher: HttpxFetcher,
    kols: list[Kol],
    *,
    force: bool,
    on_progress,
) -> tuple[list[tuple[Kol, dict]], list[tuple[Kol, str]]]:
    """阶段：邮箱域反查。从 email@domain 抓网站首页 → 抽社交账号 → 填 C/D/E。

    只处理：(1) profile_url+channel_url 双空，或 (2) platform/handle 空。
    其它情况跳过（已经在前面平台爬取阶段处理过）。
    """
    updated: list[tuple[Kol, dict]] = []
    missed: list[tuple[Kol, str]] = []
    total = len(kols)

    async def _one(kol: Kol):
        # 只处理确实缺 C/D/E 的
        has_url = kol.profile_url or kol.channel_url
        has_handle = kol.social_handle
        has_platform = kol.platform
        if has_url and has_handle and has_platform and not force:
            return  # 都满了，跳过

        domain = _email_domain(kol.email)
        if not domain:
            missed.append((kol, "通用邮箱或无邮箱域（无网站可反查）"))
            return

        # 子域（如 support.dandingle.store）失败时退到主域（dandingle.store）。
        # 邮箱域常是 support/shop/mail 这类业务子域，但社交链接多放在主站首页。
        candidate_domains = [domain]
        if domain.count(".") >= 2:
            parts = domain.split(".")
            # 取最后两段（或最后三段，针对 co.uk 这种二级 TLD）
            tld_2 = ".".join(parts[-2:])
            two_level_tlds = {"co.uk", "com.au", "co.jp", "com.br", "co.kr"}
            root = ".".join(parts[-3:]) if tld_2 in two_level_tlds else tld_2
            if root != domain and root not in candidate_domains:
                candidate_domains.append(root)

        html = ""
        used_url = ""
        for dom in candidate_domains:
            for scheme in ("https", "http"):
                url = f"{scheme}://{dom}/"
                fetched = await http_fetcher.fetch_text(url)
                if fetched and len(fetched) >= 1000:
                    html = fetched
                    used_url = url
                    break
            if html:
                break

        if not html or len(html) < 1000:
            missed.append((kol, f"网站抓取失败 ({len(html)} chars): {candidate_domains}"))
            return

        socials = _extract_socials_from_html(html)
        if not socials:
            missed.append((kol, f"网站首页未抽到社交链接: {used_url}"))
            return

        changes = _apply_socials_to_kol(kol, socials, force=force)
        # company_site 用实际抓到内容的 URL（主域优先于邮箱子域）。
        # _apply_socials_to_kol 故意不处理 company_site，集中在这里统一用 used_url。
        if force or _is_empty(kol.company_site):
            changes["company_site"] = {"from": kol.company_site, "to": used_url}
            kol.company_site = used_url
        if changes:
            updated.append((kol, changes))
        else:
            missed.append((kol, "解析到社交链接但无字段需更新"))

    await gather_pool(kols, getattr(settings, "CRAWLER_MAX_CONCURRENCY_CHANNEL", 6), _one)
    if on_progress:
        on_progress(total, total, "domain_lookup")
    return updated, missed


async def _domain_lookup_wrapper(
    kols: list[Kol], *, force: bool, on_progress
) -> tuple[list[tuple[Kol, dict]], list[tuple[Kol, str]]]:
    """``_process_domain_lookup`` 的同步可调用包装：自管 HttpxFetcher 生命周期。

    之所以需要这层包装：``run_backfill`` 是同步函数，调 ``asyncio.run`` 跑
    异步任务；``HttpxFetcher.aclose`` 是 async 不能在 finally 里直接 await。
    本函数把 fetcher 创建/关闭包在同一个 async 上下文里。
    """
    http_fetcher = HttpxFetcher()
    try:
        return await _process_domain_lookup(
            http_fetcher, kols, force=force, on_progress=on_progress
        )
    finally:
        await http_fetcher.aclose()


def _profile_or_channel_url(kol: Kol) -> Optional[str]:
    """IG/TT/X 优先 profile_url，其次 channel_url。"""
    return (kol.profile_url or kol.channel_url or "").strip() or None


# 国家占位符黑名单：YouTube country 字段偶尔返回 "/"、空串等无意义值，必须过滤。
_COUNTRY_PLACEHOLDERS = {"", "/", "-", "unknown", "n/a", "na", "none", "null"}


def _clean_country(raw: Optional[str]) -> Optional[str]:
    """清洗国家字段：占位符 / 过短值返回 None。"""
    if raw is None:
        return None
    s = str(raw).strip()
    if s.lower() in _COUNTRY_PLACEHOLDERS:
        return None
    if len(s) < 2:
        return None
    # 顺便把 US/GB 等两位国家码规范化为大写，与 DB 里现有中文/英文混用兼容
    if len(s) == 2 and s.isalpha():
        return s.upper()
    return s


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _collect_targets(
    db: Session, platform: Optional[str], limit: Optional[int]
) -> list[Kol]:
    """挑出 followers/country/niche/position 中至少有一个空缺的 KOL。

    platform 传规范化前的原值即可，这里 ILIKE 容错。
    空缺判定走 :func:`_is_empty`：包含 0、空串、占位符（'/'、'-' 等）。
    """
    q = db.query(Kol).filter(Kol.email.isnot(None), Kol.email != "")
    # 任何目标字段为空都纳入候选；空 = None / 0 / 占位符。
    # SQL 层只过滤 None 与 0/''；占位符（如 country='/'）在 Python 层二次过滤。
    q = q.filter(
        (Kol.followers.is_(None)) | (Kol.followers == 0)
        | (Kol.country.is_(None)) | (Kol.country == "")
        | (Kol.niche.is_(None)) | (Kol.niche == "")
        | (Kol.position.is_(None)) | (Kol.position == "")
    )
    if platform:
        q = q.filter(Kol.platform.ilike(platform))
    rows = q.order_by(Kol.id.asc()).all()
    if limit:
        rows = rows[:limit]
    # Python 层过滤占位符（SQL 不好表达"in _COUNTRY_PLACEHOLDERS"）
    return [r for r in rows if any(
        _is_empty(getattr(r, f)) for f in _TARGET_FIELDS
    )]


def _collect_domain_targets(
    db: Session, limit: Optional[int]
) -> list[Kol]:
    """挑出 C/D/E 列（platform/social_handle/profile_url+channel_url）有缺失的 KOL。

    邮箱域反查阶段的目标集合：那些前面平台爬取阶段没填上 URL 的（Snov 来源的
    旧 KOL 典型情况：platform 标了 youtube 但 URL/handle 空）。
    通用邮箱（gmail/outlook 等）的 KOL 直接跳过 —— 没网站可反查。
    """
    q = db.query(Kol).filter(Kol.email.isnot(None), Kol.email != "")
    # profile_url 和 channel_url 都空，或 social_handle 空，或 platform 空
    q = q.filter(
        ((Kol.profile_url.is_(None)) | (Kol.profile_url == ""))
        & ((Kol.channel_url.is_(None)) | (Kol.channel_url == ""))
        | (Kol.social_handle.is_(None)) | (Kol.social_handle == "")
        | (Kol.platform.is_(None)) | (Kol.platform == "")
    )
    rows = q.order_by(Kol.id.asc()).all()
    if limit:
        rows = rows[:limit]
    # Python 层：排除通用邮箱（无网站可反查）
    return [r for r in rows if _email_domain(r.email) is not None]


def _is_empty(value) -> bool:
    """字段是否视为空：None / 空串 / 占位符（'/', '-', 'unknown' 等）都算空。

    这样既能尊重人工填的真实值，又能让历史导入的占位符被回填覆盖。
    """
    if value is None:
        return True
    if isinstance(value, str):
        s = value.strip().lower()
        return s in _COUNTRY_PLACEHOLDERS or s == ""
    # int / 其他类型：0 视为空（followers=0 是缺失，业务约定）
    return value == 0


def _apply_parse_to_kol(
    kol: Kol, parsed: Optional[ProfileParse], *, force: bool
) -> dict:
    """把解析结果增量填入 KOL 对象（仅填空，除非 force）。返回字段变更明细。"""
    changes: dict = {}
    if parsed is None:
        return changes
    # followers：统一同步到 followers + subscribers（业务约定冗余保持一致）
    if parsed.followers and (force or _is_empty(kol.followers)):
        if kol.followers != parsed.followers:
            changes["followers"] = {"from": kol.followers, "to": parsed.followers}
            kol.followers = parsed.followers
            # subscribers 同步（仅空时）
            if force or _is_empty(kol.subscribers):
                kol.subscribers = parsed.followers
    # country
    cleaned_country = _clean_country(parsed.country)
    if cleaned_country and (force or _is_empty(kol.country)):
        changes["country"] = {"from": kol.country, "to": cleaned_country}
        kol.country = cleaned_country
    # niche：报价单「内容定位」列源头
    if parsed.niche_hint and (force or _is_empty(kol.niche)):
        changes["niche"] = {"from": kol.niche, "to": parsed.niche_hint}
        kol.niche = parsed.niche_hint
    # position：报价单「社会身份」列；解析器没明确社会身份时，用 niche_hint 兜底
    if parsed.niche_hint and (force or _is_empty(kol.position)):
        changes["position"] = {"from": kol.position, "to": parsed.niche_hint}
        kol.position = parsed.niche_hint
    return changes


def _normalize_platform_in_db(kol: Kol) -> bool:
    """顺带把 platform 大小写归一（YouTube/youtube → YouTube）。返回是否变更。"""
    if not kol.platform:
        return False
    norm = _PLATFORM_NORMALIZE.get(kol.platform.strip().lower())
    if norm and norm != kol.platform:
        kol.platform = norm
        return True
    return False


async def _process_youtube(
    kols: list[Kol],
    *,
    force: bool,
    on_progress,
) -> tuple[list[tuple[Kol, dict]], list[tuple[Kol, str]]]:
    """处理 YouTube 批：httpx + about 页正则。"""
    http_fetcher = HttpxFetcher()
    updated: list[tuple[Kol, dict]] = []
    missed: list[tuple[Kol, str]] = []
    total = len(kols)
    done = 0

    async def _one(kol: Kol):
        nonlocal done
        about_url = _youtube_about_url(kol)
        if not about_url:
            missed.append((kol, "无 channel_url/profile_url"))
            return
        html = await http_fetcher.fetch_text(about_url)
        if not html or len(html) < 5000:
            missed.append((kol, f"about 页抓取失败 ({len(html)} chars)"))
            return
        title = youtube.meta_content(html, "title").replace(" - YouTube", "").strip()
        description = youtube.full_channel_description(html)
        subscriber_text = youtube.field_from_html(html, "subscriberCountText")
        followers = normalize.parse_subscriber_count(subscriber_text)
        country, _ = normalize.resolve_country(html, description, title)

        # YouTube 走统一结构，复用 _apply_parse_to_kol
        niche_hint = None
        # 优先用视频标题推断 niche（如果已有 recent_videos）
        if kol.recent_videos:
            from services.crawler.profile_parsers import _infer_niche
            niche_hint = _infer_niche(*(kol.recent_videos or []))
        if not niche_hint:
            from services.crawler.profile_parsers import _infer_niche
            niche_hint = _infer_niche(title, description)

        parsed = ProfileParse(
            followers=followers,
            country=_clean_country(country),
            niche_hint=niche_hint,
            title=title or None,
            description=description or None,
            source="youtube.about",
        )
        changes = _apply_parse_to_kol(kol, parsed if followers else None, force=force)
        _normalize_platform_in_db(kol)
        if changes:
            updated.append((kol, changes))
        elif not followers:
            missed.append((kol, "about 页未解析到 subscribers"))

    await gather_pool(kols, getattr(settings, "CRAWLER_MAX_CONCURRENCY_CHANNEL", 6), _one)
    done = total
    if on_progress:
        on_progress(done, total, "youtube")
    await http_fetcher.aclose()
    return updated, missed


async def _process_social_platform(
    kols: list[Kol],
    *,
    platform_key: str,
    force: bool,
    on_progress,
) -> tuple[list[tuple[Kol, dict]], list[tuple[Kol, str]]]:
    """处理 IG/TT/X 批：Playwright + meta/JSON 解析。"""
    pw = _PwHtmlFetcher()
    updated: list[tuple[Kol, dict]] = []
    missed: list[tuple[Kol, str]] = []
    total = len(kols)

    async def _one(kol: Kol):
        url = _profile_or_channel_url(kol)
        if not url:
            missed.append((kol, "无 profile_url/channel_url"))
            return
        html = await pw.fetch_html(url)
        if not html or len(html) < 2000:
            missed.append((kol, f"主页抓取失败 ({len(html)} chars)"))
            return
        parsed = parse_profile(platform_key, html)
        if parsed is None or parsed.followers is None:
            missed.append((kol, "解析失败（疑似登录墙或页面改版）"))
            return
        changes = _apply_parse_to_kol(kol, parsed, force=force)
        _normalize_platform_in_db(kol)
        if changes:
            updated.append((kol, changes))
        else:
            missed.append((kol, "解析成功但无字段变更（已填满？）"))

    # IG/TT/X 反爬严，并发降到 3
    await gather_pool(kols, 3, _one)
    if on_progress:
        on_progress(total, total, platform_key)
    await pw.aclose()
    return updated, missed


def _write_missed_csv(
    missed: list[tuple[Kol, str]], batch: str, out_dir: Path
) -> Optional[Path]:
    """把失败行写到 CSV。返回文件路径，没有失败返回 None。"""
    if not missed:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"backfill_missed_{batch}.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["kol_id", "name", "platform", "url", "reason"])
        for kol, reason in missed:
            url = _profile_or_channel_url(kol) or _youtube_about_url(kol) or ""
            w.writerow([kol.id, kol.name, kol.platform or "", url, reason])
    return path


def run_backfill(
    *,
    platform: Optional[str] = None,
    commit: bool = False,
    force: bool = False,
    limit: Optional[int] = None,
    on_progress=None,
    missed_dir: Optional[Path] = None,
) -> dict:
    """执行补全。返回统计 dict。

    Args:
        platform: 限定平台（不区分大小写）；None 则全部平台，按 YouTube → IG/TT/X 顺序。
        commit: True 才写库；False 仅预览（dry-run）。
        force: True 覆盖非空字段；False 只填空（幂等默认）。
        limit: 每平台最多取多少条（调试用）。
        on_progress: 回调 (done, total, phase)。
        missed_dir: missed.csv 输出目录；None 默认 backend/logs/。
    """
    missed_dir = missed_dir or (Path(__file__).resolve().parent.parent / "logs")
    batch = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    stats: dict = {
        "batch": batch,
        "platform": platform or "all",
        "commit": commit,
        "force": force,
        "limit": limit,
        "started_at": datetime.utcnow().isoformat(),
        "per_platform": {},
        "total_updated": 0,
        "total_missed": 0,
        "missed_csv": None,
    }

    # 平台处理顺序：YouTube 先（httpx 快、成功率高），再 IG/TT/X（慢、易失败）
    norm = _PLATFORM_NORMALIZE.get((platform or "").strip().lower())
    if platform and not norm:
        raise ValueError(f"未知平台：{platform}（支持 youtube/instagram/tiktok/x）")

    if norm == "YouTube" or platform is None:
        order = ["YouTube"]
        if platform is None:
            order += ["Instagram", "TikTok", "X"]
    else:
        order = [norm]

    db = SessionLocal()
    all_updated: list[tuple[Kol, dict]] = []
    all_missed: list[tuple[Kol, str]] = []
    try:
        try:
            for plat in order:
                targets = _collect_targets(db, plat, limit)
                if not targets:
                    stats["per_platform"][plat] = {
                        "targets": 0, "updated": 0, "missed": 0
                    }
                    continue

                if plat == "YouTube":
                    updated, missed = asyncio.run(_process_youtube(
                        targets, force=force, on_progress=on_progress
                    ))
                else:
                    updated, missed = asyncio.run(_process_social_platform(
                        targets, platform_key=plat.lower(), force=force,
                        on_progress=on_progress,
                    ))
                all_updated.extend(updated)
                all_missed.extend(missed)
                stats["per_platform"][plat] = {
                    "targets": len(targets),
                    "updated": len(updated),
                    "missed": len(missed),
                }

            # 末尾阶段：邮箱域反查（补 C/D/E 三列：platform/social_handle/profile_url）
            # 只跑一次，处理全表（不限平台），且需要同时处理前面阶段没成功填 URL 的行。
            # --platform 限定单平台时也跑（因为 Snov 来源的 platform 经常是空）。
            domain_targets = _collect_domain_targets(db, limit)
            if domain_targets:
                updated, missed = asyncio.run(_domain_lookup_wrapper(
                    domain_targets,
                    force=force, on_progress=on_progress,
                ))
                all_updated.extend(updated)
                all_missed.extend(missed)
                stats["per_platform"]["domain_lookup"] = {
                    "targets": len(domain_targets),
                    "updated": len(updated),
                    "missed": len(missed),
                }
            else:
                stats["per_platform"]["domain_lookup"] = {
                    "targets": 0, "updated": 0, "missed": 0
                }

            # 写 missed CSV（即使 dry-run 也写，便于排查）
            csv_path = _write_missed_csv(all_missed, batch, missed_dir)
            stats["missed_csv"] = str(csv_path) if csv_path else None
            stats["total_updated"] = len(all_updated)
            stats["total_missed"] = len(all_missed)

            if commit and all_updated:
                db.commit()
            elif commit and not all_updated:
                # 没有 update 也 commit 一次，把 platform 归一化等顺带改动落库
                db.commit()
            else:
                db.rollback()
        except Exception:
            db.rollback()
            raise
    finally:
        db.close()

    stats["finished_at"] = datetime.utcnow().isoformat()
    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_preview(stats: dict, preview_rows: int = 20) -> None:
    mode = "写库" if stats["commit"] else "预览(dry-run)"
    print(f"\n=== KOL 字段补全 [{mode}] force={stats['force']} ===")
    print(f"批次: {stats['batch']}")
    for plat, s in stats["per_platform"].items():
        print(
            f"  {plat:10s} 目标 {s['targets']:4d}  更新 {s['updated']:4d}  "
            f"失败 {s['missed']:4d}"
        )
    print(
        f"合计: 更新 {stats['total_updated']}  失败 {stats['total_missed']}  "
        f"missed.csv: {stats.get('missed_csv') or '(无)'}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="爬取公开主页回填 KOL 字段")
    parser.add_argument(
        "--platform",
        type=str,
        default="",
        help="限定平台：youtube/instagram/tiktok/x（空则全部）",
    )
    parser.add_argument("--commit", action="store_true", help="实际写库（默认 dry-run）")
    parser.add_argument("--force", action="store_true", help="覆盖非空字段（默认只填空）")
    parser.add_argument(
        "--limit", type=int, default=None, help="每平台最多处理多少条（调试用）"
    )
    parser.add_argument("--verbose", action="store_true", help="打印 DEBUG 日志")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    def _progress(done, total, phase):
        print(f"  [{phase}] {done}/{total}", flush=True)

    platform = args.platform.strip() or None
    stats = run_backfill(
        platform=platform,
        commit=args.commit,
        force=args.force,
        limit=args.limit,
        on_progress=_progress,
    )
    _print_preview(stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
