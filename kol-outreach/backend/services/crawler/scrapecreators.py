"""ScrapeCreators 社交平台抓取 API 异步客户端。

覆盖 YouTube / TikTok / Instagram 三平台的：
  - 关键词搜索（/v1/{platform}/search 等）
  - profile 抓取（/v1/{platform}/profile、/v1/youtube/channel）

设计要点：
  - 不实现 fetcher.Fetcher 协议：那是 HTML/MX 用的，JSON API 形状不同。
  - 失败不抛异常（与 fetcher 的 "never raise" 契约一致），返回空列表/None。
  - 字段抽取做防御式多键匹配（ScrapeCreators 文档未列字段名，实际响应以运行时为准）。
  - 并发限速复用 fetcher.gather_pool。

key 只从 config.settings.SCRAPECREATORS_API_KEY 读，绝不写进代码或 skill。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional
from urllib.parse import quote

import httpx

from config import settings
from services.crawler.fetcher import gather_pool

logger = logging.getLogger(__name__)

# 请求头里的认证键名（ScrapeCreators 固定用 x-api-key）。
_API_KEY_HEADER = "x-api-key"

# 单次 API 请求超时（秒）。ScrapeCreators 抓 profile 可能偏慢，放宽到 30s。
_REQUEST_TIMEOUT = 30.0

# ===== 平台 → 端点路径映射 =====
# ScrapeCreators 不同平台的端点路径不一致（YouTube 用 /channel，TT/IG 用 /profile），
# 且 search 端点路径各异。这里集中维护，避免散落在调用处。
# 参数名与路径已通过真实 API 调用验证（2026-07-23）：
#   - YouTube: /v1/youtube/channel?handle=...   （不是 username）
#   - TikTok:  /v1/tiktok/profile?handle=...    （不是 username）
#   - IG:      /v1/instagram/profile?handle=... （不是 username）
_PLATFORM_CONFIG: dict[str, dict[str, str]] = {
    "youtube": {
        "profile_path": "/v1/youtube/channel",
        "profile_param": "handle",
    },
    "tiktok": {
        "profile_path": "/v1/tiktok/profile",
        "profile_param": "handle",
    },
    "instagram": {
        "profile_path": "/v1/instagram/profile",
        "profile_param": "handle",
    },
}


def _supported_platforms() -> list[str]:
    """返回当前客户端支持的平台（小写）。"""
    return list(_PLATFORM_CONFIG.keys())


class ScrapeCreatorsClient:
    """ScrapeCreators REST API 异步客户端。

    用法::

        client = ScrapeCreatorsClient()
        rows = await client.discover_and_build_rows(
            products=["Dola"], queries=[("Dola", "lifestyle creator tips")],
            on_progress=cb,
        )
        await client.aclose()
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        # ``None`` means "use configured default"; an explicit empty string
        # means "disable this client".  Do not use ``or`` here, otherwise
        # tests and per-call opt-out paths silently fall back to production
        # credentials.
        self._api_key = (
            settings.SCRAPECREATORS_API_KEY or ""
            if api_key is None
            else api_key
        )
        resolved_base_url = (
            settings.SCRAPECREATORS_BASE_URL or ""
            if base_url is None
            else base_url
        )
        self._base_url = resolved_base_url.rstrip("/")
        # headers 在 __init__ 构建一次，复用给每个请求。
        self._headers = {_API_KEY_HEADER: self._api_key}
        # 单个 AsyncClient 贯穿整个 client 生命周期（连接池复用）。
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def is_configured(self) -> bool:
        """是否具备发请求的最小条件（有 key + 有 base_url）。"""
        return bool(self._api_key) and bool(self._base_url)

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers=self._headers,
                timeout=httpx.Timeout(_REQUEST_TIMEOUT),
                follow_redirects=True,
            )
        return self._client

    async def _get(self, path: str, params: dict[str, str]) -> Optional[dict]:
        """发一次 GET 请求，返回解析后的 JSON dict。

        失败（网络/超时/非 2xx/解析失败）一律返回 None，不抛异常。
        """
        if not self.is_configured:
            return None
        client = await self._ensure_client()
        try:
            resp = await client.get(path, params=params)
            if resp.status_code != 200:
                logger.warning(
                    "ScrapeCreators %s 返回 %s: %s",
                    path, resp.status_code, resp.text[:200],
                )
                return None
            data = resp.json()
            # 有些端点把数据包在 {"data": [...]} 里，统一解包。
            if isinstance(data, dict) and isinstance(data.get("data"), (list, dict)):
                return data
            return {"data": data}
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("ScrapeCreators %s 请求失败: %s: %s", path, type(exc).__name__, exc)
            return None

    # ------------------------------------------------------------------
    # 搜索
    # ------------------------------------------------------------------
    async def search_platform(
        self, platform: str, query: str
    ) -> list[dict]:
        """在指定平台用关键词搜索，返回**规范化后的条目列表**。

        三平台 search 端点的响应结构各不相同（2026-07-23 真实 API 验证）：
          - YouTube /v1/youtube/search: 顶层 videos/shorts（每条内嵌 channel{handle}），
            channels 通常为空。一个频道可能因多个视频重复出现。
          - TikTok  /v1/tiktok/search/users: user_list（每条内嵌 user_info{uniqueId,...}）。
          - IG      /v1/instagram/search/profiles: profiles（每条即扁平 user dict）。

        这里把三平台的差异抹平，统一返回"扁平化的账号 dict"列表，使下游
        extract_username / extract_display_name（无 platform 参数调用时）能直接命中：
          - YouTube: {"handle": ..., "name": ..., "channel": 原channel}
          - TikTok:  {**user_info}（顶层即 uniqueId/nickname/signature）
          - IG:      原条目（已扁平，username/full_name/biography/follower_count）

        完整字段（粉丝/邮箱/国家）仍由后续 fetch_profile 单独抓取；这里只负责
        发现唯一账号并去重。
        """
        path = self._search_path(platform)
        if not path:
            return []
        data = await self._get(path, {"query": query})
        if not data:
            return []
        # _get 会把整个响应包在 {"data": {...}} 下，取真正的响应体。
        resp = data.get("data") if isinstance(data.get("data"), dict) else data

        p = platform.lower()
        if p == "youtube":
            return self._normalize_youtube_search(resp)
        if p == "tiktok":
            return self._normalize_tiktok_search(resp)
        if p == "instagram":
            return self._normalize_instagram_search(resp)
        return []

    @staticmethod
    def _normalize_youtube_search(resp: dict) -> list[dict]:
        """YouTube search：从 videos/shorts 的内嵌 channel 提取唯一频道。

        一个频道的多个视频会重复出现，这里按 handle 去重（首个保留）。
        channels 字段官方常返回空，但若非空也并入。
        """
        out: list[dict] = []
        seen: set[str] = set()
        for key in ("channels", "videos", "shorts", "playlists", "lives"):
            for item in (resp.get(key) or []):
                if not isinstance(item, dict):
                    continue
                channel = item.get("channel") if key != "channels" else item
                if not isinstance(channel, dict):
                    continue
                handle = (channel.get("handle") or channel.get("channel_handle")
                          or channel.get("vanityChannelUrl"))
                if not handle:
                    continue
                handle = str(handle).strip().lstrip("@").split("/")[-1]
                if not handle or handle.lower() in seen:
                    continue
                seen.add(handle.lower())
                out.append({
                    "handle": handle,
                    "name": channel.get("title") or channel.get("name"),
                    "channel": channel.get("id"),
                })
        return out

    @staticmethod
    def _normalize_tiktok_search(resp: dict) -> list[dict]:
        """TikTok search/users：user_list 每条内嵌 user_info（snake_case 字段），提扁平到顶层。"""
        out: list[dict] = []
        seen: set[str] = set()
        for item in (resp.get("user_list") or []):
            user = item.get("user_info") if isinstance(item, dict) else None
            if not isinstance(user, dict):
                continue
            # TikTok user_info 用 snake_case：unique_id（不是 uniqueId）
            uid = user.get("unique_id") or user.get("uniqueId")
            if not uid:
                continue
            uid = str(uid).strip().lstrip("@")
            if not uid or uid.lower() in seen:
                continue
            seen.add(uid.lower())
            # 扁平化：顶层即 user_info，保留 unique_id/nickname/signature 等供 extract_* 用
            out.append(dict(user))
        return out

    @staticmethod
    def _normalize_instagram_search(resp: dict) -> list[dict]:
        """IG search/profiles：profiles 每条已是扁平 user dict，去重后直返。"""
        out: list[dict] = []
        seen: set[str] = set()
        for item in (resp.get("profiles") or []):
            if not isinstance(item, dict):
                continue
            uname = item.get("username")
            if not uname:
                continue
            uname = str(uname).strip().lstrip("@")
            if not uname or uname.lower() in seen:
                continue
            seen.add(uname.lower())
            out.append(item)
        return out

    def _search_path(self, platform: str) -> str:
        """各平台 search 端点路径（文档确认的路径）。"""
        p = platform.lower()
        if p == "youtube":
            return "/v1/youtube/search"
        if p == "tiktok":
            return "/v1/tiktok/search/users"
        if p == "instagram":
            return "/v1/instagram/search/profiles"
        return ""

    # ------------------------------------------------------------------
    # profile 抓取
    # ------------------------------------------------------------------
    async def fetch_profile(self, platform: str, handle: str) -> Optional[dict]:
        """抓取单个账号的完整 profile，返回原始 JSON dict 或 None。

        返回的是**原始响应结构**（保留嵌套），由 extract_* 方法按 platform 解包。
        YouTube 顶层；TikTok {user, stats}；Instagram {data: {user}}。
        """
        cfg = _PLATFORM_CONFIG.get(platform.lower())
        if not cfg:
            return None
        # handle 去掉可能残留的 URL 前缀和 @
        clean = handle.strip().lstrip("@").split("/")[-1]
        data = await self._get(cfg["profile_path"], {cfg["profile_param"]: clean})
        if not data:
            return None
        # IG 返回 {"data": {"user": {...}}}；YouTube/TikTok 顶层即数据。
        # 这里统一返回能被 _node 正确解包的原始结构。
        if isinstance(data.get("data"), dict):
            # IG: 把 data.user 提到顶层 user，保持 {user: {...}} 结构给 _node 用
            ig_user = data["data"].get("user")
            if isinstance(ig_user, dict):
                return {"user": ig_user, **{k: v for k, v in data["data"].items() if k != "user"}}
            return data["data"]
        return data if isinstance(data, dict) else None

    # ------------------------------------------------------------------
    # 字段抽取（按平台差异化，2026-07-23 真实响应验证）
    # ------------------------------------------------------------------
    # 三平台响应结构不同（已用真实 API 调用确认）：
    #   YouTube:  扁平。顶层直接有 handle/name/subscriberCount/email/country/description。
    #   TikTok:   嵌套。user.uniqueId/user.nickname/user.signature + stats.followerCount/stats.heart。
    #             无 email、无 country。
    #   Instagram: 嵌套。data.user.username/data.user.biography/data.user.business_email/
    #             data.user.edge_followed_by.count。无 country。
    # 这些方法接受 fetch_profile 解包后的 dict，内部按 platform 取正确路径。

    @staticmethod
    def _pick(profile: dict, *keys: str) -> Any:
        """从 profile 里按顺序取第一个非空的 key 值。"""
        for k in keys:
            v = profile.get(k)
            if v not in (None, "", 0):
                return v
        return None

    def _node(self, profile: dict, platform: str) -> dict:
        """按平台取承载核心字段的子 dict。"""
        p = platform.lower()
        if p == "tiktok":
            # TikTok 数据在 user.* 和 stats.* 两个子树里，合并成一个视图
            user = profile.get("user") or {}
            stats = profile.get("stats") or {}
            merged = {**user, **stats}  # stats 键优先级低，放后面
            return merged if merged else profile
        if p == "instagram":
            # IG 数据在 data.user.*
            return profile.get("user") or (profile.get("data") or {}).get("user") or profile
        # YouTube 扁平
        return profile

    @staticmethod
    def _to_int(value: Any) -> Optional[int]:
        """把各种形态的数字（"1.2M"、1234、"1234"）转成 int。"""
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return int(value)
        s = str(value).strip().lower().replace(",", "")
        if not s:
            return None
        mult = 1
        if s.endswith("k"):
            mult, s = 1_000, s[:-1]
        elif s.endswith("m"):
            mult, s = 1_000_000, s[:-1]
        elif s.endswith("b"):
            mult, s = 1_000_000_000, s[:-1]
        try:
            return int(float(s) * mult)
        except ValueError:
            return None

    def extract_username(self, profile: dict, platform: str = "") -> Optional[str]:
        node = self._node(profile, platform) if platform else profile
        v = self._pick(node, "uniqueId", "unique_id", "handle", "username", "channel_handle")
        return str(v).strip().lstrip("@") if v else None

    def extract_display_name(self, profile: dict, platform: str = "") -> Optional[str]:
        node = self._node(profile, platform) if platform else profile
        v = self._pick(node, "name", "nickname", "fullName", "full_name", "display_name", "displayName", "title")
        return str(v).strip() if v else None

    def extract_followers(self, profile: dict, platform: str = "") -> Optional[int]:
        node = self._node(profile, platform) if platform else profile
        # IG 的 edge_followed_by 是嵌套 {"count": N}
        efb = node.get("edge_followed_by")
        if isinstance(efb, dict) and "count" in efb:
            return self._to_int(efb["count"])
        v = self._pick(
            node, "followerCount", "follower_count", "followers",
            "subscriberCount", "subscriber_count", "subscribers",
        )
        return self._to_int(v)

    def extract_avg_views(self, profile: dict, platform: str = "") -> Optional[int]:
        node = self._node(profile, platform) if platform else profile
        # TikTok 用 heart（总点赞）近似热度；YouTube 有 viewCount
        v = self._pick(
            node, "viewCount", "view_count", "views",
            "avgViews", "avg_views", "heart", "heartCount",
        )
        return self._to_int(v)

    def extract_email(self, profile: dict, platform: str = "") -> Optional[str]:
        node = self._node(profile, platform) if platform else profile
        v = self._pick(node, "email", "business_email", "businessEmail", "public_email", "contact_email")
        return str(v).strip().lower() if v else None

    def extract_country(self, profile: dict, platform: str = "") -> Optional[str]:
        # 仅 YouTube profile 有 country 字段；TikTok/IG 无国家，交由调用方走简介推断
        node = self._node(profile, platform) if platform else profile
        v = self._pick(node, "country", "region", "countryCode", "country_code")
        return str(v).strip() if v else None

    def extract_description(self, profile: dict, platform: str = "") -> Optional[str]:
        node = self._node(profile, platform) if platform else profile
        v = self._pick(node, "description", "signature", "biography", "bio")
        return str(v).strip() if v else None

    def extract_profile_url(self, profile: dict, platform: str) -> Optional[str]:
        node = self._node(profile, platform) if platform else profile
        # YouTube 返回顶层 channel 字段；IG/TikTok 可能有 external_url
        v = self._pick(profile, "channel", "url", "profile_url", "link")
        if v:
            return str(v)
        username = self.extract_username(profile, platform)
        if not username:
            return None
        p = platform.lower()
        if p == "youtube":
            return f"https://www.youtube.com/@{username}"
        if p == "tiktok":
            return f"https://www.tiktok.com/@{username}"
        if p == "instagram":
            return f"https://www.instagram.com/{username}/"
        return None

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
