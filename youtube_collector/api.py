from __future__ import annotations

import time
from typing import Iterable

import requests


class YouTubeApiError(RuntimeError):
    pass


class YouTubeApiClient:
    BASE_URL = "https://www.googleapis.com/youtube/v3"

    def __init__(self, api_key: str, timeout: float = 20, interval: float = 0.2):
        self.api_key = api_key.strip()
        self.timeout = timeout
        self.interval = interval
        self.session = requests.Session()

    def _get(self, resource: str, **params) -> dict:
        params["key"] = self.api_key
        try:
            response = self.session.get(
                f"{self.BASE_URL}/{resource}", params=params, timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise YouTubeApiError(f"网络请求失败：{exc}") from exc
        if self.interval:
            time.sleep(self.interval)
        try:
            payload = response.json()
        except ValueError as exc:
            raise YouTubeApiError(f"YouTube 返回了非 JSON 响应（HTTP {response.status_code}）") from exc
        if not response.ok:
            message = payload.get("error", {}).get("message", response.text[:300])
            raise YouTubeApiError(f"YouTube API 错误（HTTP {response.status_code}）：{message}")
        return payload

    def search_videos(self, keyword: str, page_token: str | None = None) -> dict:
        params = {
            "part": "snippet",
            "q": keyword,
            "type": "video",
            "maxResults": 50,
            "order": "relevance",
        }
        if page_token:
            params["pageToken"] = page_token
        return self._get("search", **params)

    def get_channels(self, channel_ids: Iterable[str]) -> dict[str, dict]:
        ids = list(dict.fromkeys(channel_ids))
        result: dict[str, dict] = {}
        for start in range(0, len(ids), 50):
            payload = self._get(
                "channels",
                part="snippet,statistics",
                id=",".join(ids[start : start + 50]),
                maxResults=50,
            )
            result.update({item["id"]: item for item in payload.get("items", [])})
        return result

    def get_videos(self, video_ids: Iterable[str]) -> dict[str, dict]:
        ids = list(dict.fromkeys(video_ids))
        result: dict[str, dict] = {}
        for start in range(0, len(ids), 50):
            payload = self._get(
                "videos",
                part="snippet,statistics",
                id=",".join(ids[start : start + 50]),
                maxResults=50,
            )
            result.update({item["id"]: item for item in payload.get("items", [])})
        return result

    def get_regions(self, language: str = "en_US") -> dict[str, str]:
        """Return supported region codes mapped to their localized display names."""
        payload = self._get("i18nRegions", part="snippet", hl=language)
        result: dict[str, str] = {}
        for item in payload.get("items", []):
            snippet = item.get("snippet", {})
            code = str(snippet.get("gl") or item.get("id") or "").upper()
            name = str(snippet.get("name") or "").strip()
            if code:
                result[code] = name
        return result
