"""Snov 集成管理接口（受看板登录保护）。"""
import hashlib
import logging
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator
from sqlalchemy import or_
from sqlalchemy.orm import Session

from db import get_db
from models import Kol, Message, Thread
from services.ai_intent import analyze_intent, intent_to_thread_status
from services.attachments import extract_attachments, extract_links_from_text, merge_attachments
from services.email_content import clean_email_body, is_html_email_body
from services.snov_client import get_snov_client
from services.snov_contacts import sync_snov_contacts
from services.snov_export import SnovListCreateError, create_snov_list_from_kols

router = APIRouter()
logger = logging.getLogger(__name__)


def _client_or_502():
    try:
        return get_snov_client()
    except Exception as exc:
        raise HTTPException(502, f"Snov client 初始化失败: {exc}")


@router.get("/status")
def snov_status():
    """验证凭据并返回 Campaign / webhook 数量，不返回任何密钥。"""
    try:
        client = _client_or_502()
        return {
            "connected": True,
            "campaign_count": len(client.list_campaigns()),
            "webhook_count": len(client.list_webhooks()),
        }
    except Exception as exc:
        raise HTTPException(502, f"Snov API 连接失败: {exc}")


@router.get("/campaigns")
def list_campaigns():
    try:
        campaigns = get_snov_client().list_campaigns()
        return [
            {
                "id": str(item.get("id") or item.get("hash") or ""),
                "name": (item.get("campaign") or {}).get("name") if isinstance(item.get("campaign"), dict) else item.get("campaign"),
                "status": item.get("status"),
                "list_id": item.get("list_id"),
            }
            for item in campaigns
        ]
    except Exception as exc:
        raise HTTPException(502, f"获取 Snov Campaign 失败: {exc}")


@router.get("/webhooks")
def list_webhooks():
    try:
        return get_snov_client().list_webhooks()
    except Exception as exc:
        raise HTTPException(502, f"获取 Snov webhook 失败: {exc}")


@router.post("/sync-contacts")
def sync_contacts(db: Session = Depends(get_db)):
    """Import every current Snov prospect list into the local contact table."""
    try:
        return sync_snov_contacts(db, get_snov_client())
    except Exception as exc:
        raise HTTPException(502, f"同步 Snov 联系人失败: {exc}")


class CreateProspectListFromKolsIn(BaseModel):
    list_name: str = Field(min_length=1, max_length=200)
    kol_ids: list[int] = Field(min_length=1, max_length=500)

    @field_validator("list_name")
    @classmethod
    def normalize_list_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("名单名称不能为空")
        return value


@router.post("/prospect-lists/from-kols")
def create_prospect_list_from_kols(
    body: CreateProspectListFromKolsIn,
    db: Session = Depends(get_db),
):
    """Create a fresh Snov list and fill it with selected pending KOLs."""
    try:
        return create_snov_list_from_kols(
            db,
            get_snov_client(),
            list_name=body.list_name,
            kol_ids=body.kol_ids,
        )
    except SnovListCreateError as exc:
        raise HTTPException(502, f"创建 Snov 待发送名单失败: {exc}")


class CreateWebhookIn(BaseModel):
    endpoint_url: HttpUrl
    event_object: Literal["campaign_email", "campaign_reply"]
    event_action: Literal["sent", "received", "autoreply_received"]

    @model_validator(mode="after")
    def validate_event_pair(self):
        allowed_actions = {
            "campaign_email": {"sent"},
            "campaign_reply": {"received", "autoreply_received"},
        }
        if self.event_action not in allowed_actions[self.event_object]:
            raise ValueError("event_action is not valid for event_object")
        return self


@router.post("/webhooks")
def create_webhook(body: CreateWebhookIn):
    """创建 Snov webhook；开放已发、普通回信和自动回复事件。"""
    try:
        existing = get_snov_client().list_webhooks()
        endpoint = str(body.endpoint_url)
        for hook in existing:
            if (
                hook.get("event_object") == body.event_object
                and hook.get("event_action") == body.event_action
                and hook.get("end_point", hook.get("endpoint_url")) == endpoint
            ):
                return {"status": "exists", "webhook": hook}
        hook = get_snov_client().create_webhook(
            body.event_object,
            body.event_action,
            endpoint,
        )
        return {"status": "created", "webhook": hook}
    except Exception as exc:
        raise HTTPException(502, f"创建 Snov webhook 失败: {exc}")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _parse_snov_time(value: Any) -> datetime:
    """把 Snov 的 receivedAt 转为数据库使用的 UTC naive datetime。"""
    if isinstance(value, (int, float)):
        return datetime.utcfromtimestamp(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            pass
    return datetime.utcnow()


def _historical_message_id(
    campaign_id: str,
    prospect_email: str,
    subject: str,
    body: str,
    received_at: Any,
) -> str:
    """all-replies 未提供邮件 ID，以稳定指纹保证重复同步不会重复入库。"""
    identity = "|".join(
        [campaign_id, prospect_email, subject, body, _text(received_at)]
    )
    return f"snov:history:{hashlib.sha256(identity.encode()).hexdigest()}"


def _attach_campaign(thread: Thread, campaign_id: str, campaign_name: str) -> None:
    """只为同一活动或尚未归属的历史会话补上活动标记。"""
    if not campaign_id:
        return
    if thread.campaign_id in (None, "", campaign_id):
        thread.campaign_id = campaign_id
        if campaign_name:
            thread.campaign_name = campaign_name


@router.post("/sync-replies")
def sync_historical_replies(db: Session = Depends(get_db)):
    """补拉 Snov Campaign 的历史回信；可安全重复执行，不会创建重复消息。"""
    try:
        client = get_snov_client()
        campaigns = client.list_campaigns()
    except Exception as exc:
        raise HTTPException(502, f"获取 Snov Campaign 失败: {exc}")

    result = {
        "campaigns": len(campaigns),
        "created_kols": 0,
        "created_threads": 0,
        "created_messages": 0,
        "duplicates": 0,
        "skipped": 0,
        "errors": [],
    }

    try:
        for campaign in campaigns:
            campaign_id = _text(campaign.get("id") or campaign.get("hash"))
            campaign_name = _text(
                (campaign.get("campaign") or {}).get("name")
                if isinstance(campaign.get("campaign"), dict)
                else campaign.get("campaign")
            )
            if not campaign_id:
                result["skipped"] += 1
                continue

            try:
                payload = client.get_campaign_replies(campaign_id)
            except Exception as exc:
                result["errors"].append({"campaign_id": campaign_id, "error": str(exc)})
                continue

            records = payload.get("data", payload) if isinstance(payload, dict) else payload
            if not isinstance(records, list):
                result["errors"].append({"campaign_id": campaign_id, "error": "Snov 返回格式异常"})
                continue

            for record in records:
                if not isinstance(record, dict):
                    result["skipped"] += 1
                    continue

                prospect = record.get("prospect") if isinstance(record.get("prospect"), dict) else {}
                prospect_email = _text(
                    record.get("prospectEmail")
                    or record.get("prospect_email")
                    or record.get("email")
                    or prospect.get("email")
                ).lower()
                if not prospect_email or "@" not in prospect_email:
                    result["skipped"] += 1
                    continue

                prospect_name = _text(
                    record.get("prospectName")
                    or record.get("prospect_name")
                    or record.get("name")
                    or prospect.get("name")
                ) or prospect_email.split("@", 1)[0]
                record_campaign_id = _text(record.get("campaignId") or record.get("campaign_id")) or campaign_id
                record_campaign_name = _text(record.get("campaign") or record.get("campaign_name")) or campaign_name
                replies = record.get("replies") or record.get("emails")
                # v2 all-replies groups replies under each prospect, while the
                # email-only v1 endpoint may return one reply per record.
                if not isinstance(replies, list):
                    if any(record.get(key) not in (None, "") for key in ("message", "body", "subject")):
                        replies = [record]
                    else:
                        result["skipped"] += 1
                        continue

                for reply in replies:
                    if not isinstance(reply, dict):
                        result["skipped"] += 1
                        continue

                    subject = _text(
                        reply.get("subject")
                        or reply.get("emailSubject")
                        or reply.get("email_subject")
                    ) or "Snov 历史回信"
                    raw_body = _text(
                        reply.get("message")
                        or reply.get("body")
                        or reply.get("bodyText")
                        or reply.get("body_text")
                        or reply.get("emailBody")
                        or reply.get("email_body")
                    )
                    body = clean_email_body(raw_body) or "[Snov 历史回信；正文为空]"
                    received_value = (
                        reply.get("receivedAt")
                        or reply.get("received_at")
                        or reply.get("timestamp")
                        or record.get("visitedAt")
                        or record.get("visited_at")
                    )
                    attachments = merge_attachments(
                        extract_attachments(record, reply),
                        extract_links_from_text(raw_body),
                    )
                    recipient_email = _text(
                        reply.get("recipientEmail")
                        or reply.get("recipient_email")
                        or reply.get("to")
                        or record.get("senderEmail")
                        or record.get("sender_email")
                    ).lower()
                    received_at = _parse_snov_time(received_value)
                    message_id = _historical_message_id(
                        campaign_id,
                        prospect_email,
                        subject,
                        body,
                        received_value,
                    )
                    # Snov 的 prospectId 每次读取都会变化，不能用于幂等键。旧版
                    # 导入过的数据则通过邮件内容与接收时间识别，兼容一次性补数据。
                    existing_message = db.query(Message).filter(
                        or_(
                            Message.message_id == message_id,
                            (
                                (Message.direction == "inbound")
                                & (Message.from_email == prospect_email)
                                & (Message.subject == subject)
                                & (Message.body_text == body)
                                & (Message.received_at == received_at)
                            ),
                        )
                    ).first()
                    if existing_message:
                        if attachments and not existing_message.attachments:
                            existing_message.attachments = attachments
                        _attach_campaign(
                            existing_message.thread,
                            record_campaign_id,
                            record_campaign_name,
                        )
                        result["duplicates"] += 1
                        continue

                    kol = db.query(Kol).filter(Kol.email == prospect_email).first()
                    if not kol:
                        kol = Kol(name=prospect_name, email=prospect_email, status="in_conversation")
                        db.add(kol)
                        db.flush()
                        result["created_kols"] += 1

                    thread_query = db.query(Thread).filter(
                        Thread.kol_id == kol.id,
                        Thread.status != "closed",
                    )
                    thread = thread_query.filter(
                        Thread.campaign_id == record_campaign_id
                    ).order_by(Thread.updated_at.desc()).first()
                    if not thread:
                        # 兼容本次部署前的历史会话，找到后立即补上活动归属。
                        thread = thread_query.filter(
                            Thread.campaign_id.is_(None)
                        ).order_by(Thread.updated_at.desc()).first()
                    if not thread:
                        thread = Thread(
                            kol_id=kol.id,
                            subject=subject,
                            status="open",
                            campaign_id=record_campaign_id,
                            campaign_name=record_campaign_name,
                        )
                        db.add(thread)
                        db.flush()
                        result["created_threads"] += 1
                    else:
                        _attach_campaign(thread, record_campaign_id, record_campaign_name)

                    message = Message(
                        thread_id=thread.id,
                        direction="inbound",
                        from_email=prospect_email,
                        # Use the campaign mailbox when Snov returns it; never
                        # fabricate an address when the API omits the field.
                        to_email=recipient_email or "unknown@snov.local",
                        subject=subject,
                        body_text=body,
                        body_html=raw_body if is_html_email_body(raw_body) else None,
                        attachments=attachments,
                        message_id=message_id,
                        received_at=received_at,
                    )
                    db.add(message)
                    thread.reply_count = (thread.reply_count or 0) + 1
                    kol.status = "in_conversation"

                    analysis = analyze_intent(body, kol_name=kol.name, subject=subject)
                    if analysis:
                        message.ai_analysis = analysis
                        thread.last_intent = analysis.get("intent")
                        thread.intent_score = analysis.get("intent_score", 0)
                        thread.ai_summary = analysis.get("summary")
                        next_status = intent_to_thread_status(analysis)
                        if next_status:
                            thread.status = next_status
                    result["created_messages"] += 1

        db.commit()
        logger.info(
            "Snov sync complete: campaigns=%s created_kols=%s created_threads=%s "
            "created_messages=%s duplicates=%s skipped=%s errors=%s",
            result["campaigns"],
            result["created_kols"],
            result["created_threads"],
            result["created_messages"],
            result["duplicates"],
            result["skipped"],
            len(result["errors"]),
        )
    except Exception:
        db.rollback()
        raise

    return result
