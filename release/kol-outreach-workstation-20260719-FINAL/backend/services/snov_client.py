"""Snov.io API 客户端：管理联系人、webhook 和 Campaign 数据。"""
import logging
import time
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

from config import settings

logger = logging.getLogger(__name__)


class SnovClient:
    """按需获取 OAuth token，避免把 token 持久化到数据库或日志。"""

    def __init__(self, client_id: Optional[str] = None, client_secret: Optional[str] = None):
        self.client_id = client_id or settings.SNOV_CLIENT_ID
        self.client_secret = client_secret or settings.SNOV_CLIENT_SECRET
        self.base_url = settings.SNOV_API_BASE.rstrip("/")
        self._token: Optional[str] = None
        self._token_expires_at: float = 0
        # 所有请求都使用 Snov API 根地址；httpx 的相对路径请求必须配置 base_url。
        self._client = httpx.Client(base_url=self.base_url, timeout=30, trust_env=False)

    def _get_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        if not self.client_id or not self.client_secret:
            raise RuntimeError("SNOV_CLIENT_ID / SNOV_CLIENT_SECRET 未配置")

        response = self._client.post(
            "/v1/oauth/access_token",
            json={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials",
            },
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise RuntimeError("Snov 未返回 access_token")
        self._token = token
        # Official tokens currently last one hour. Refresh one minute early.
        expires_in = int(payload.get("expires_in") or 3600)
        self._token_expires_at = time.monotonic() + max(60, expires_in - 60)
        return token

    def _request(self, method: str, path: str, *, params: Optional[dict] = None, json_body: Optional[dict] = None) -> dict | list:
        token = self._get_token()
        response = self._authorized_request(method, path, token, params, json_body)
        if response.status_code == 401:
            self._token = None
            self._token_expires_at = 0
            token = self._get_token()
            response = self._authorized_request(method, path, token, params, json_body)
        response.raise_for_status()
        return response.json()

    def _authorized_request(
        self,
        method: str,
        path: str,
        token: str,
        params: Optional[dict],
        json_body: Optional[dict],
    ) -> httpx.Response:
        return self._client.request(
            method,
            path,
            params=params,
            json=json_body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )

    def list_webhooks(self) -> list[dict]:
        payload = self._request("GET", "/v2/webhooks")
        data = payload.get("data", []) if isinstance(payload, dict) else []
        # Snov v2 有时会将每个对象再包一层 data。
        return [item.get("data", item) if isinstance(item, dict) else item for item in data]

    def create_webhook(self, event_object: str, event_action: str, endpoint_url: str) -> dict:
        payload = self._request(
            "POST",
            "/v2/webhooks",
            json_body={
                "event_object": event_object,
                "event_action": event_action,
                "endpoint_url": endpoint_url,
            },
        )
        return payload.get("data", payload) if isinstance(payload, dict) else payload

    def list_campaigns(self) -> list[dict]:
        """旧版 Campaign 接口仍要求 access_token 查询参数。"""
        token = self._get_token()
        response = self._client.get("/v1/get-user-campaigns", params={"access_token": token})
        response.raise_for_status()
        payload: Any = response.json()
        if isinstance(payload, list):
            return payload
        return payload.get("data", []) if isinstance(payload, dict) else []

    def list_prospect_lists(self) -> list[dict]:
        """Return every prospect list in the connected Snov account."""
        token = self._get_token()
        response = self._client.get(
            "/v1/get-user-lists", params={"access_token": token}
        )
        response.raise_for_status()
        payload: Any = response.json()
        return payload if isinstance(payload, list) else payload.get("data", [])

    def _legacy_post(self, path: str, fields: list[tuple[str, Any]]) -> Any:
        """Call a legacy form-encoded v1 endpoint and refresh once on 401."""
        token = self._get_token()

        def send(access_token: str) -> httpx.Response:
            body = urlencode([("access_token", access_token), *fields]).encode("utf-8")
            return self._client.post(
                path,
                content=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

        response = send(token)
        if response.status_code == 401:
            self._token = None
            self._token_expires_at = 0
            response = send(self._get_token())
        response.raise_for_status()
        return response.json()

    def create_prospect_list(self, name: str) -> Any:
        """Create an empty prospect list and return Snov's raw response."""
        return self._legacy_post("/v1/lists", [("name", name)])

    def add_prospect_to_list(self, list_id: str, prospect: dict) -> dict:
        """Add or update one prospect without creating an email duplicate."""
        fields: list[tuple[str, Any]] = [
            ("email", prospect["email"]),
            ("listId", str(list_id)),
            ("updateContact", "true"),
            ("createDuplicates", "false"),
        ]
        scalar_fields = {
            "fullName": "fullName",
            "firstName": "firstName",
            "lastName": "lastName",
            "country": "country",
            "locality": "locality",
            "position": "position",
            "companyName": "companyName",
            "companySite": "companySite",
        }
        for source, target in scalar_fields.items():
            value = prospect.get(source)
            if value not in (None, ""):
                fields.append((target, str(value)))
        for phone in prospect.get("phones") or []:
            if phone:
                fields.append(("phones", str(phone)))
        linkedin = prospect.get("linkedin_url")
        if linkedin:
            fields.append(("socialLinks[linkedIn]", str(linkedin)))

        payload = self._legacy_post("/v1/add-prospect-to-list", fields)
        return payload if isinstance(payload, dict) else {"success": False, "errors": payload}

    def get_prospects_in_list(
        self, list_id: str, page: int = 1, per_page: int = 5000
    ) -> dict:
        """Read one page of contacts from a Snov prospect list (max 5,000)."""
        token = self._get_token()
        response = self._client.post(
            "/v1/prospect-list",
            params={
                "access_token": token,
                "listId": str(list_id),
                "page": page,
                "perPage": min(max(per_page, 1), 5000),
            },
        )
        response.raise_for_status()
        payload: Any = response.json()
        return payload if isinstance(payload, dict) else {"prospects": []}

    def get_campaign_replies(self, campaign_id: str) -> dict | list:
        """Read email replies only, excluding LinkedIn replies."""
        # Despite the current knowledge-base spelling (`campaign_id`), the
        # live legacy endpoint requires `campaignId` and the token in query.
        token = self._get_token()
        response = self._client.get(
            "/v1/get-emails-replies",
            params={"access_token": token, "campaignId": campaign_id},
        )
        if response.status_code == 401:
            self._token = None
            self._token_expires_at = 0
            response = self._client.get(
                "/v1/get-emails-replies",
                params={"access_token": self._get_token(), "campaignId": campaign_id},
            )
        response.raise_for_status()
        return response.json()


_client: Optional[SnovClient] = None


def get_snov_client() -> SnovClient:
    global _client
    if _client is None:
        _client = SnovClient()
    return _client
