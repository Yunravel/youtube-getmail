from __future__ import annotations

import csv
import logging
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from .api import YouTubeApiClient
from .contact_parser import extract_public_contacts


CSV_FIELDS = [
    "搜索关键词", "页码", "视频标题", "视频链接", "当前视频播放数", "博主名称",
    "博主链接", "频道ID", "频道链接", "国家", "电报链接", "WhatsApp链接",
    "推特链接", "脸书链接", "Instagram链接", "TikTok链接", "粉丝数", "视频总数",
    "总观看次数", "注册日期", "联系说明", "联系详情", "邮箱状态",
]


@dataclass(frozen=True)
class CollectOptions:
    keywords: list[str]
    countries: set[str]
    min_subscribers: int
    max_subscribers: int
    pages: int
    output_file: Path
    email_only: bool = False
    scan_public_websites: bool = True


def _number(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _matches(subscribers: int, country: str, options: CollectOptions) -> bool:
    if options.countries and country.upper() not in options.countries:
        return False
    if options.min_subscribers and subscribers < options.min_subscribers:
        return False
    if options.max_subscribers and subscribers > options.max_subscribers:
        return False
    return True


def _resolve_countries(api: YouTubeApiClient, values: set[str]) -> set[str]:
    if not values:
        return set()

    is_code = lambda value: len(value) == 2 and value.isascii() and value.isalpha()
    resolved = {value.upper() for value in values if is_code(value)}
    names = {value for value in values if not is_code(value)}
    if not names:
        return resolved

    aliases: dict[str, str] = {}
    for language in ("zh_CN", "en_US"):
        for code, name in api.get_regions(language).items():
            aliases[name.strip().casefold()] = code

    unknown = []
    for value in names:
        code = aliases.get(value.strip().casefold())
        if code:
            resolved.add(code)
        else:
            unknown.append(value)
    if unknown:
        raise ValueError(f"无法识别国家/地区：{'、'.join(sorted(unknown))}。请改用两位代码。")
    return resolved


class YouTubeCollector:
    def __init__(
        self,
        api: YouTubeApiClient,
        logger: logging.Logger,
        status: Callable[[str], None] | None = None,
    ):
        self.api = api
        self.logger = logger
        self.status = status or (lambda _message: None)

    def run(self, options: CollectOptions, stop_event: threading.Event) -> int:
        options.output_file.parent.mkdir(parents=True, exist_ok=True)
        country_codes = _resolve_countries(self.api, options.countries)
        if country_codes:
            options = replace(options, countries=country_codes)
        already_exists = options.output_file.exists() and options.output_file.stat().st_size > 0
        written = 0
        seen: set[tuple[str, str]] = set()

        with options.output_file.open("a", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            if not already_exists:
                writer.writeheader()
                handle.flush()

            for keyword in options.keywords:
                page = 1
                token = None
                while not stop_event.is_set():
                    if options.pages != -1 and page > options.pages:
                        break
                    self._emit(f"搜索“{keyword}”第 {page} 页…")
                    payload = self.api.search_videos(keyword, token)
                    search_items = payload.get("items", [])
                    if not search_items:
                        break

                    video_ids = [x.get("id", {}).get("videoId", "") for x in search_items]
                    video_ids = [x for x in video_ids if x]
                    videos = self.api.get_videos(video_ids)
                    channel_ids = [
                        videos[x].get("snippet", {}).get("channelId", "")
                        for x in video_ids if x in videos
                    ]
                    channels = self.api.get_channels(x for x in channel_ids if x)

                    for video_id in video_ids:
                        if stop_event.is_set():
                            break
                        video = videos.get(video_id, {})
                        video_snippet = video.get("snippet", {})
                        channel_id = video_snippet.get("channelId", "")
                        unique_key = (keyword.casefold(), channel_id)
                        if not channel_id or unique_key in seen:
                            continue
                        seen.add(unique_key)
                        channel = channels.get(channel_id, {})
                        snippet = channel.get("snippet", {})
                        stats = channel.get("statistics", {})
                        subscribers = _number(stats.get("subscriberCount"))
                        country = str(snippet.get("country", "")).upper()
                        if not _matches(subscribers, country, options):
                            continue

                        contacts = extract_public_contacts(snippet.get("description", ""))
                        if options.email_only and not contacts["email"]:
                            continue
                        contact_values = [v for v in contacts.values() if v]
                        custom = snippet.get("customUrl", "")
                        public_url = f"https://www.youtube.com/{custom}" if custom else f"https://www.youtube.com/channel/{channel_id}"
                        row = {
                            "搜索关键词": keyword,
                            "页码": page,
                            "视频标题": video_snippet.get("title", ""),
                            "视频链接": f"https://www.youtube.com/watch?v={video_id}",
                            "当前视频播放数": _number(video.get("statistics", {}).get("viewCount")),
                            "博主名称": snippet.get("title", ""),
                            "博主链接": public_url,
                            "频道ID": channel_id,
                            "频道链接": f"https://www.youtube.com/channel/{channel_id}",
                            "国家": country,
                            "电报链接": contacts["telegram"],
                            "WhatsApp链接": contacts["whatsapp"],
                            "推特链接": contacts["twitter"],
                            "脸书链接": contacts["facebook"],
                            "Instagram链接": contacts["instagram"],
                            "TikTok链接": contacts["tiktok"],
                            "粉丝数": subscribers,
                            "视频总数": _number(stats.get("videoCount")),
                            "总观看次数": _number(stats.get("viewCount")),
                            "注册日期": snippet.get("publishedAt", ""),
                            "联系说明": "仅解析频道公开简介" if contact_values else "未在公开简介中发现",
                            "联系详情": contacts["email"],
                            "邮箱状态": "已获取" if contacts["email"] else "未发现（API 模式无法判断人工验证入口）",
                        }
                        writer.writerow(row)
                        handle.flush()
                        written += 1
                        self._emit(f"已保存 {written} 条：{row['博主名称']}")

                    token = payload.get("nextPageToken")
                    if not token:
                        break
                    page += 1

        self._emit("已停止" if stop_event.is_set() else f"采集完成，共新增 {written} 条")
        return written

    def _emit(self, message: str) -> None:
        self.logger.info(message)
        self.status(message)
