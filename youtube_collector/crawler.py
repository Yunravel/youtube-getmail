from __future__ import annotations

import csv
import logging
import re
import threading
import time
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, quote_plus, urlparse

from .collector import CSV_FIELDS, CollectOptions
from .contact_parser import extract_public_contacts


class CrawlerError(RuntimeError):
    pass


NUMBER_RE = re.compile(r"([\d,.]+)\s*([KMB]|万|亿)?", re.IGNORECASE)
COUNTRY_ALIASES = {
    "US": {"US", "USA", "UNITED STATES", "美国", "美國"},
    "GB": {"GB", "UK", "UNITED KINGDOM", "英国", "英國"},
    "CA": {"CA", "CANADA", "加拿大"},
    "AU": {"AU", "AUSTRALIA", "澳大利亚", "澳洲", "澳大利亞"},
    "DE": {"DE", "GERMANY", "德国", "德國"},
    "FR": {"FR", "FRANCE", "法国", "法國"},
    "JP": {"JP", "JAPAN", "日本"},
    "KR": {"KR", "SOUTH KOREA", "KOREA", "韩国", "韓國"},
    "SG": {"SG", "SINGAPORE", "新加坡"},
    "MY": {"MY", "MALAYSIA", "马来西亚", "馬來西亞"},
    "TH": {"TH", "THAILAND", "泰国", "泰國"},
    "VN": {"VN", "VIETNAM", "越南"},
    "ID": {"ID", "INDONESIA", "印度尼西亚", "印尼", "印度尼西亞"},
    "PH": {"PH", "PHILIPPINES", "菲律宾", "菲律賓"},
    "IN": {"IN", "INDIA", "印度"},
    "BR": {"BR", "BRAZIL", "巴西"},
    "MX": {"MX", "MEXICO", "墨西哥"},
    "ES": {"ES", "SPAIN", "西班牙"},
    "IT": {"IT", "ITALY", "意大利", "義大利"},
    "NL": {"NL", "NETHERLANDS", "荷兰", "荷蘭"},
    "AE": {"AE", "UNITED ARAB EMIRATES", "UAE", "阿联酋", "阿聯酋"},
    "SA": {"SA", "SAUDI ARABIA", "沙特阿拉伯"},
    "NZ": {"NZ", "NEW ZEALAND", "新西兰", "紐西蘭"},
    "ZA": {"ZA", "SOUTH AFRICA", "南非"},
    "HK": {"HK", "HONG KONG", "香港"},
    "TW": {"TW", "TAIWAN", "台湾", "台灣"},
}


def parse_localized_number(text: str) -> int:
    value = (text or "").replace("\u00a0", " ")
    match = NUMBER_RE.search(value)
    if not match:
        return 0
    number = float(match.group(1).replace(",", ""))
    multiplier = {
        "K": 1_000,
        "M": 1_000_000,
        "B": 1_000_000_000,
        "万": 10_000,
        "亿": 100_000_000,
    }.get((match.group(2) or "").upper(), 1)
    return int(number * multiplier)


def country_matches(country: str, requested: set[str]) -> bool:
    if not requested:
        return True
    actual = (country or "").strip().upper()
    if not actual:
        return False
    for wanted in requested:
        normalized = wanted.strip().upper()
        if normalized == actual:
            return True
        for aliases in COUNTRY_ALIASES.values():
            upper_aliases = {alias.upper() for alias in aliases}
            if normalized in upper_aliases and actual in upper_aliases:
                return True
        try:
            import pycountry

            record = (
                pycountry.countries.get(alpha_2=normalized)
                if len(normalized) == 2
                else pycountry.countries.lookup(wanted.strip())
            )
            if record:
                names = {
                    getattr(record, "name", "").upper(),
                    getattr(record, "official_name", "").upper(),
                    getattr(record, "common_name", "").upper(),
                }
                if actual in names:
                    return True
        except (ImportError, LookupError):
            pass
    return False


def unwrap_youtube_redirect(url: str) -> str:
    parsed = urlparse(url or "")
    if parsed.netloc.endswith("youtube.com") and parsed.path == "/redirect":
        target = parse_qs(parsed.query).get("q", [])
        if target:
            return target[0]
    return url


def parse_about_rows(rows: list[str]) -> dict[str, object]:
    result: dict[str, object] = {
        "country": "",
        "published_at": "",
        "subscribers": 0,
        "video_count": 0,
        "view_count": 0,
    }
    for raw in rows:
        line = " ".join((raw or "").split())
        lowered = line.casefold()
        if not line or "youtube.com/" in lowered:
            continue
        if "subscriber" in lowered or "订阅" in line or "訂閱" in line:
            result["subscribers"] = parse_localized_number(line)
        elif "video" in lowered or "视频" in line or "影片" in line:
            result["video_count"] = parse_localized_number(line)
        elif "view" in lowered or "观看" in line or "觀看" in line:
            result["view_count"] = parse_localized_number(line)
        elif "joined" in lowered or "注册" in line or "註冊" in line:
            result["published_at"] = line
        elif lowered not in {"more info", "更多信息", "更多資訊"}:
            result["country"] = line
    return result


class BrowserCrawler:
    """Collect public YouTube data by driving an installed Chrome or Edge browser."""

    def __init__(
        self,
        logger: logging.Logger,
        status: Callable[[str], None] | None = None,
        show_browser: bool = False,
        interval: float = 0.8,
        timeout_seconds: float = 30,
    ):
        self.logger = logger
        self.status = status or (lambda _message: None)
        self.show_browser = show_browser
        self.interval = interval
        self.timeout_ms = int(timeout_seconds * 1000)

    def run(self, options: CollectOptions, stop_event: threading.Event) -> int:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise CrawlerError("缺少浏览器组件，请重新运行“启动工具.cmd”安装依赖。") from exc

        options.output_file.parent.mkdir(parents=True, exist_ok=True)
        exists = options.output_file.exists() and options.output_file.stat().st_size > 0
        written = 0
        seen: set[tuple[str, str]] = set()

        with sync_playwright() as playwright:
            browser = self._launch_browser(playwright)
            context = browser.new_context(
                locale="en-US",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0 Safari/537.36"
                ),
                viewport={"width": 1365, "height": 900},
            )
            page = context.new_page()
            page.set_default_timeout(self.timeout_ms)

            try:
                with options.output_file.open("a", newline="", encoding="utf-8-sig") as handle:
                    writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
                    if not exists:
                        writer.writeheader()
                        handle.flush()

                    for keyword in options.keywords:
                        if stop_event.is_set():
                            break
                        limit = None if options.pages == -1 else options.pages * 50
                        self._emit(f"爬虫搜索“{keyword}”…")
                        results = self._search_results(page, keyword, limit, stop_event)
                        self._emit(f"搜索页发现 {len(results)} 个公开视频，开始读取频道资料…")

                        for index, result in enumerate(results):
                            if stop_event.is_set():
                                break
                            channel_url = result.get("channel_url", "")
                            unique_key = (keyword.casefold(), channel_url.casefold())
                            if not channel_url or unique_key in seen:
                                continue
                            seen.add(unique_key)
                            self._emit(f"读取频道：{result.get('channel_name') or channel_url}")
                            try:
                                channel = self._read_channel(page, channel_url)
                            except Exception as exc:
                                self.logger.warning("频道页面读取失败 %s: %s", channel_url, exc)
                                self._emit(f"跳过无法读取的频道：{channel_url}")
                                continue

                            subscribers = int(channel["subscribers"])
                            country = str(channel["country"])
                            if options.min_subscribers and subscribers < options.min_subscribers:
                                continue
                            if options.max_subscribers and subscribers > options.max_subscribers:
                                continue
                            if not country_matches(country, options.countries):
                                continue

                            contacts = extract_public_contacts(
                                "\n".join([str(channel["description"]), *channel["links"]])
                            )
                            has_contacts = any(contacts.values())
                            row = {
                                "搜索关键词": keyword,
                                "页码": index // 50 + 1,
                                "视频标题": result.get("title", ""),
                                "视频链接": result.get("video_url", ""),
                                "当前视频播放数": result.get("views", 0),
                                "博主名称": result.get("channel_name", ""),
                                "博主链接": channel_url,
                                "频道ID": channel.get("channel_id", ""),
                                "频道链接": channel.get("canonical_url", channel_url),
                                "国家": country,
                                "电报链接": contacts["telegram"],
                                "WhatsApp链接": contacts["whatsapp"],
                                "推特链接": contacts["twitter"],
                                "脸书链接": contacts["facebook"],
                                "Instagram链接": contacts["instagram"],
                                "TikTok链接": contacts["tiktok"],
                                "粉丝数": subscribers,
                                "视频总数": channel["video_count"],
                                "总观看次数": channel["view_count"],
                                "注册日期": channel["published_at"],
                                "联系说明": "来自频道公开简介及公开外链" if has_contacts else "未发现公开联系方式",
                                "联系详情": contacts["email"],
                            }
                            writer.writerow(row)
                            handle.flush()
                            written += 1
                            self._emit(f"已保存 {written} 条：{row['博主名称']}")
                            if self.interval:
                                time.sleep(self.interval)
            finally:
                context.close()
                browser.close()

        self._emit("已停止" if stop_event.is_set() else f"爬虫采集完成，共新增 {written} 条")
        return written

    def _launch_browser(self, playwright):
        errors = []
        for channel in ("chrome", "msedge"):
            try:
                return playwright.chromium.launch(
                    channel=channel,
                    headless=not self.show_browser,
                    args=["--disable-blink-features=AutomationControlled"],
                )
            except Exception as exc:
                errors.append(f"{channel}: {exc}")
        raise CrawlerError(
            "未找到可用的 Chrome 或 Edge 浏览器。请先安装其中一个。\n" + "\n".join(errors)
        )

    def _search_results(
        self,
        page,
        keyword: str,
        limit: int | None,
        stop_event: threading.Event,
    ) -> list[dict]:
        page.goto(
            f"https://www.youtube.com/results?search_query={quote_plus(keyword)}",
            wait_until="domcontentloaded",
            timeout=self.timeout_ms,
        )
        self._dismiss_consent(page)
        try:
            page.wait_for_selector("ytd-video-renderer", timeout=self.timeout_ms)
        except Exception as exc:
            body = page.locator("body").inner_text(timeout=5_000).casefold()
            if any(marker in body for marker in ("unusual traffic", "captcha", "验证码", "驗證碼")):
                raise CrawlerError("YouTube 要求人机验证。请勾选“显示浏览器窗口”后重试。") from exc
            raise CrawlerError(f"搜索页没有加载出视频结果：{keyword}") from exc
        stable_rounds = 0
        previous = 0
        for _ in range(100):
            if stop_event.is_set():
                break
            count = page.locator("ytd-video-renderer").count()
            if limit is not None and count >= limit:
                break
            stable_rounds = stable_rounds + 1 if count == previous else 0
            if stable_rounds >= 4:
                break
            previous = count
            page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
            page.wait_for_timeout(900)

        rows = page.eval_on_selector_all(
            "ytd-video-renderer",
            """nodes => nodes.map(node => {
                const title = node.querySelector('a#video-title');
                const channel = node.querySelector('ytd-channel-name a');
                const metadata = Array.from(node.querySelectorAll('#metadata-line span'))
                    .map(item => item.textContent.trim());
                return {
                    title: title?.textContent.trim() || '',
                    video_url: title?.href || '',
                    channel_name: channel?.textContent.trim() || '',
                    channel_url: channel?.href || '',
                    view_text: metadata[0] || ''
                };
            })""",
        )
        unique = []
        seen_urls = set()
        for row in rows:
            video_url = row.get("video_url", "")
            if not video_url or video_url in seen_urls:
                continue
            seen_urls.add(video_url)
            row["views"] = parse_localized_number(row.pop("view_text", ""))
            unique.append(row)
            if limit is not None and len(unique) >= limit:
                break
        return unique

    def _read_channel(self, page, channel_url: str) -> dict[str, object]:
        about_url = channel_url.rstrip("/") + "/about"
        page.goto(about_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        try:
            page.wait_for_selector("ytd-about-channel-renderer", timeout=12_000)
        except Exception:
            pass
        data = page.evaluate(
            """() => {
                const dialogs = Array.from(document.querySelectorAll('tp-yt-paper-dialog[role=dialog]'));
                const dialog = dialogs.find(item => item.querySelector('ytd-about-channel-renderer'));
                const about = dialog?.querySelector('#about-container');
                const descriptions = Array.from(about?.children || [])
                    .filter(item => item.tagName === 'YT-ATTRIBUTED-STRING')
                    .map(item => item.innerText.trim())
                    .filter(Boolean);
                const rows = Array.from(dialog?.querySelectorAll('#additional-info-container tr') || [])
                    .map(row => row.innerText.trim()).filter(Boolean);
                const links = Array.from(dialog?.querySelectorAll('#links-section a') || [])
                    .map(link => link.href || link.innerText.trim()).filter(Boolean);
                const canonical = document.querySelector('link[rel=canonical]')?.href || '';
                const identifier = document.querySelector('meta[itemprop=identifier]')?.content || '';
                return {
                    description: descriptions.length > 1 ? descriptions[1] : '',
                    rows,
                    links,
                    canonical_url: canonical,
                    channel_id: identifier
                };
            }"""
        )
        if not data.get("rows"):
            raise CrawlerError("频道公开资料弹窗未加载，页面结构可能已变化。")
        details = parse_about_rows(data.get("rows", []))
        details.update(
            {
                "description": data.get("description", ""),
                "links": [unwrap_youtube_redirect(link) for link in data.get("links", [])],
                "canonical_url": data.get("canonical_url", channel_url),
                "channel_id": data.get("channel_id", ""),
            }
        )
        return details

    @staticmethod
    def _dismiss_consent(page) -> None:
        for label in ("Accept all", "I agree", "全部接受", "同意"):
            try:
                button = page.get_by_role("button", name=label, exact=False)
                if button.count():
                    button.first.click(timeout=2_000)
                    page.wait_for_timeout(500)
                    return
            except Exception:
                continue

    def _emit(self, message: str) -> None:
        self.logger.info(message)
        self.status(message)
