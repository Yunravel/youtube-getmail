"""
Instantly API 客户端
封装发信、查询 lead、查询 campaign 等操作

Instantly API 文档: https://developer.instantly.ai/
核心接口:
- POST /v2/Campaigns/AddLead      推送 KOL 到 campaign(触发序列发送)
- GET  /v2/Campaigns              列出 campaign
- POST /v2/Campaigns/PauseLead    暂停某个 lead
- POST /v2/Campaigns/UpdateLead   更新 lead 自定义变量
"""
import logging
from typing import Optional
import httpx

from config import settings

logger = logging.getLogger(__name__)


class InstantlyClient:
    """Instantly API 封装"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.INSTANTLY_API_KEY
        self.base_url = settings.INSTANTLY_API_BASE
        # Instantly v2 用 Bearer token
        self._client: Optional[httpx.Client] = None

    @property
    def client(self) -> httpx.Client:
        if not self.api_key:
            raise RuntimeError("INSTANTLY_API_KEY 未配置")
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
        return self._client

    # ===== Campaign 相关 =====

    def list_campaigns(self) -> list[dict]:
        """列出所有 campaign(导入 KOL 时选哪个用)"""
        resp = self.client.get("/v2/Campaigns")
        resp.raise_for_status()
        return resp.json().get("data", [])

    # ===== Lead 相关 =====

    def add_lead_to_campaign(
        self,
        campaign_id: str,
        email: str,
        first_name: Optional[str] = None,
        custom_variables: Optional[dict] = None,
    ) -> dict:
        """
        推送一个 KOL 到 campaign(开始发首封)
        Instantly 会按 campaign 配置的节奏自动发后续跟进

        Args:
            campaign_id: Instantly campaign ID
            email: KOL 邮箱
            first_name: 名字(个性化变量 {{first_name}})
            custom_variables: 自定义变量
                例: {"personal_intro": "Hey Jimmy...", "channel_topic": "tech"}

        Returns:
            Instantly 返回的 lead 信息
        """
        payload = {
            "campaign": campaign_id,
            "email": email,
        }
        if first_name:
            payload["first_name"] = first_name
        if custom_variables:
            payload["custom_variables"] = custom_variables

        resp = self.client.post("/v2/Campaigns/AddLead", json=payload)

        if resp.status_code not in (200, 201):
            logger.error(f"Instantly 添加 lead 失败: {resp.status_code} {resp.text}")
            return {"status": "error", "detail": resp.text}

        data = resp.json()
        logger.info(f"Instantly lead 已加入 campaign {campaign_id}: {email}")
        return data

    def add_leads_batch(
        self,
        campaign_id: str,
        leads: list[dict],
    ) -> dict:
        """
        批量推送 KOL 到 campaign
        leads 格式: [{"email": "x@x.com", "first_name": "Jimmy", "variables": {...}}, ...]
        """
        success = 0
        failed = []
        for lead in leads:
            result = self.add_lead_to_campaign(
                campaign_id=campaign_id,
                email=lead["email"],
                first_name=lead.get("first_name"),
                custom_variables=lead.get("variables"),
            )
            if result.get("status") == "error":
                failed.append({"email": lead["email"], "error": result.get("detail")})
            else:
                success += 1
        return {"success": success, "failed": failed, "total": len(leads)}

    def pause_lead(self, campaign_id: str, email: str) -> dict:
        """暂停某个 lead(对方拒绝或已合作后停止跟进)"""
        resp = self.client.post(
            "/v2/Campaigns/PauseLead",
            json={"campaign": campaign_id, "email": email},
        )
        return resp.json() if resp.status_code == 200 else {"status": "error", "detail": resp.text}


# 全局单例
_instantly: Optional[InstantlyClient] = None


def get_instantly_client() -> InstantlyClient:
    global _instantly
    if _instantly is None:
        _instantly = InstantlyClient()
    return _instantly
