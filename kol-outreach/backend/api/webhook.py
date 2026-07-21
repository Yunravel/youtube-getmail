"""Snov webhook 接收端。

中台只负责同步 Snov 的收发邮件、判断意向并展示会话；实际发信和人工回复
始终留在 Snov。支持 v2 的 campaign_email/campaign_reply 事件，也兼容旧字段名。
"""
import hashlib
import hmac
import json
import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from config import settings
from db import SessionLocal, get_db
from models import Kol, Message, Thread
from services.email_content import clean_email_body, is_html_email_body
from services.attachments import extract_attachments, extract_links_from_text, merge_attachments

router = APIRouter()
logger = logging.getLogger(__name__)

SENT_EVENTS = {("campaign_email", "sent"), ("campaign_email", "first_sent")}
REPLY_EVENTS = {
    ("campaign_reply", "received"),
    ("campaign_reply", "first_received"),
    ("campaign_reply", "autoreply_received"),
}
LEGACY_REPLY_EVENTS = {"replied", "reply", "received", "autoreplied", "autoreply_received"}
LEGACY_SENT_EVENTS = {"sent", "first_sent"}


def _value(data: dict[str, Any], *names: str) -> Any:
    """在 webhook 根对象及常见 data 包装中取第一个非空字段。"""
    containers = [data]
    nested = data.get("data")
    if isinstance(nested, dict):
        containers.append(nested)
    for container in containers:
        for name in names:
            value = container.get(name)
            if value not in (None, ""):
                return value
    return None


def _dict_value(data: dict[str, Any], *names: str) -> dict[str, Any]:
    value = _value(data, *names)
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("text") or value.get("body") or value.get("content") or ""
    return str(value or "").strip()


def _normalize_email(value: Any) -> str:
    return _text(value).lower()


def _event_kind(payload: dict[str, Any]) -> Optional[str]:
    event_object = _text(_value(payload, "event_object", "object")).lower()
    event_action = _text(_value(payload, "event_action", "action")).lower()
    if (event_object, event_action) in SENT_EVENTS:
        return "outbound"
    if (event_object, event_action) in REPLY_EVENTS:
        return "inbound"

    # 兼容旧版或经 Make/Zapier 转发的字段。
    legacy_event = _text(_value(payload, "event", "event_type")).lower()
    if legacy_event in LEGACY_SENT_EVENTS:
        return "outbound"
    if legacy_event in LEGACY_REPLY_EVENTS:
        return "inbound"
    return None


def _campaign_fields(payload: dict[str, Any]) -> tuple[str, str]:
    """读取 Snov webhook 的 Campaign 归属，兼容根对象及 data 包装。"""
    campaign = _dict_value(payload, "campaign", "campaign_data")
    raw_campaign = _value(payload, "campaign")
    campaign_id = _text(
        campaign.get("id")
        or campaign.get("campaign_id")
        or _value(payload, "campaign_id", "campaignId", "campaign_hash")
    )
    campaign_name = _text(
        campaign.get("name")
        or campaign.get("title")
        or _value(payload, "campaign_name", "campaign_title")
        or (raw_campaign if isinstance(raw_campaign, str) else "")
    )
    return campaign_id, campaign_name


def _message_fields(payload: dict[str, Any], direction: str) -> dict[str, Optional[str]]:
    """归一化 Snov 及中转工具常见的邮件字段。"""
    prospect = _dict_value(payload, "prospect", "recipient", "contact")
    email_data = _dict_value(payload, "email_data", "email", "message_data")
    root_email = _value(payload, "email", "prospect_email", "recipient_email")
    prospect_email = _normalize_email(
        prospect.get("email")
        or root_email
        or _value(payload, "prospect_email", "recipient_email", "contact_email")
    )
    sender_email = _normalize_email(
        _value(payload, "sender_email", "mailbox_email", "from_email", "from")
        or email_data.get("from")
    )

    if direction == "inbound":
        from_email = _normalize_email(
            _value(payload, "from_email", "from", "reply_from") or prospect_email
        )
        to_email = _normalize_email(
            _value(payload, "to_email", "to", "recipient", "recipient_email") or sender_email
        )
        kol_email = from_email or prospect_email
    else:
        from_email = sender_email
        to_email = _normalize_email(
            _value(payload, "to_email", "to", "recipient_email") or prospect_email
        )
        kol_email = to_email or prospect_email

    raw_body = _text(
        _value(payload, "body_text", "body", "message", "text", "content", "reply", "reply_text", "reply_content")
        or email_data.get("body")
        or email_data.get("text")
        or email_data.get("content")
    )
    body = clean_email_body(raw_body)
    if not body and direction == "outbound":
        body = "[Snov 已发送此邮件；该事件未返回正文。]"

    name = _text(
        prospect.get("name")
        or prospect.get("full_name")
        or _value(payload, "prospect_name", "recipient_name", "name", "full_name")
    )
    subject = _text(_value(payload, "subject", "message_subject", "email_subject") or email_data.get("subject"))
    message_id = _text(_value(payload, "message_id", "email_message_id", "reply_id", "id"))
    in_reply_to = _text(_value(payload, "in_reply_to", "reply_to_message_id"))
    received_at = _value(payload, "received_at", "sent_at", "created_at", "timestamp", "event_time")

    return {
        "kol_email": kol_email,
        "kol_name": name,
        "from_email": from_email or "unknown@snov.local",
        "to_email": to_email or "unknown@snov.local",
        "subject": subject or "(no subject)",
        "body_text": body,
        "body_html": raw_body if is_html_email_body(raw_body) else None,
        "message_id": message_id,
        "in_reply_to": in_reply_to or None,
        "received_at": received_at,
        "attachments": merge_attachments(
            extract_attachments(payload, email_data),
            extract_links_from_text(raw_body),
        ),
    }


def _stable_message_id(direction: str, fields: dict[str, Optional[str]]) -> str:
    """Snov 未提供消息 ID 时，为重试请求生成稳定的幂等 ID。"""
    identity = "|".join(
        str(fields.get(key) or "")
        for key in ("kol_email", "from_email", "to_email", "subject", "body_text", "received_at")
    )
    return f"snov:{direction}:{hashlib.sha256(identity.encode()).hexdigest()}"


def _get_or_create_kol(db: Session, email: str, name: str) -> Kol:
    kol = db.query(Kol).filter(Kol.email == email).first()
    if kol:
        return kol
    kol = Kol(
        name=name or email.split("@", 1)[0],
        email=email,
        status="sent",
    )
    db.add(kol)
    db.flush()
    logger.info("Snov webhook 自动创建 KOL: %s", email)
    return kol


def _find_or_create_thread(
    db: Session,
    kol: Kol,
    subject: str,
    in_reply_to: Optional[str],
    campaign_id: str = "",
    campaign_name: str = "",
) -> Thread:
    def attach_campaign(thread: Thread) -> Thread:
        if campaign_id and thread.campaign_id in (None, "", campaign_id):
            thread.campaign_id = campaign_id
            if campaign_name:
                thread.campaign_name = campaign_name
        return thread

    if in_reply_to:
        replied_message = db.query(Message).filter(Message.message_id == in_reply_to).first()
        if replied_message:
            return attach_campaign(replied_message.thread)

    thread_query = db.query(Thread).filter(
        Thread.kol_id == kol.id,
        Thread.status != "closed",
    )
    thread = None
    if campaign_id:
        thread = thread_query.filter(
            Thread.campaign_id == campaign_id
        ).order_by(Thread.updated_at.desc()).first()
        if not thread:
            thread = thread_query.filter(
                Thread.campaign_id.is_(None)
            ).order_by(Thread.updated_at.desc()).first()
    else:
        thread = thread_query.order_by(Thread.updated_at.desc()).first()
    if thread:
        return attach_campaign(thread)

    thread = Thread(
        kol_id=kol.id,
        subject=subject,
        status="open",
        campaign_id=campaign_id or None,
        campaign_name=campaign_name or None,
    )
    db.add(thread)
    db.flush()
    return thread


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.utcfromtimestamp(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            pass
    return datetime.utcnow()


def analyze_inbound_message(message_id: int) -> None:
    """响应 webhook 后再运行 AI，确保 Snov 能在三秒内收到成功响应。"""
    db = SessionLocal()
    try:
        message = db.query(Message).get(message_id)
        if not message or message.direction != "inbound":
            return
        thread = message.thread
        if not thread or not thread.kol:
            return

        from services.ai_intent import analyze_intent, intent_to_thread_status

        analysis = analyze_intent(
            email_body=message.body_text or "",
            kol_name=thread.kol.name,
            subject=message.subject,
        )
        message.ai_analysis = analysis
        thread.last_intent = analysis["intent"]
        thread.intent_score = analysis["intent_score"]
        thread.ai_summary = analysis["summary"]
        new_status = intent_to_thread_status(analysis)
        if new_status:
            thread.status = new_status
        db.commit()
        logger.info("Snov 回信已分析: message=%s intent=%s", message_id, analysis["intent"])
    except Exception:
        db.rollback()
        logger.exception("Snov 回信 AI 分析失败: message=%s", message_id)
    finally:
        db.close()


@router.post("/snov")
async def snov_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """接收 Snov 已发送邮件与收到回复，保存到中台会话。"""
    if not settings.snov_webhook_is_configured:
        logger.error("拒绝 Snov webhook：SNOV_WEBHOOK_TOKEN 未安全配置")
        raise HTTPException(503, "Webhook is not configured")

    token = request.query_params.get("token", "")
    if not hmac.compare_digest(token, settings.SNOV_WEBHOOK_TOKEN):
        raise HTTPException(401, "Invalid webhook token")

    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        try:
            payload = dict(await request.form())
        except Exception:
            raise HTTPException(400, "Invalid webhook payload")

    if not isinstance(payload, dict):
        raise HTTPException(400, "Webhook payload must be an object")

    direction = _event_kind(payload)
    if not direction:
        return {"status": "ignored", "reason": "unsupported_event"}

    fields = _message_fields(payload, direction)
    kol_email = _normalize_email(fields["kol_email"])
    if not kol_email or "@" not in kol_email:
        logger.warning("Snov webhook 忽略：未解析到有效 KOL 邮箱")
        return {"status": "ignored", "reason": "no_email"}

    message_id = fields["message_id"] or _stable_message_id(direction, fields)
    existing = db.query(Message).filter(Message.message_id == message_id).first()
    if existing:
        return {"status": "duplicate", "message_id": message_id}

    kol = _get_or_create_kol(db, kol_email, _text(fields["kol_name"]))
    campaign_id, campaign_name = _campaign_fields(payload)
    thread = _find_or_create_thread(
        db,
        kol,
        _text(fields["subject"]),
        fields["in_reply_to"],
        campaign_id,
        campaign_name,
    )
    message = Message(
        thread_id=thread.id,
        direction=direction,
        from_email=_text(fields["from_email"]),
        to_email=_text(fields["to_email"]),
        subject=_text(fields["subject"]),
        body_text=_text(fields["body_text"]),
        body_html=fields["body_html"],
        attachments=fields["attachments"],
        message_id=message_id,
        in_reply_to=fields["in_reply_to"],
        received_at=_parse_datetime(fields["received_at"]),
    )
    db.add(message)
    if direction == "inbound":
        thread.reply_count = (thread.reply_count or 0) + 1
        kol.status = "in_conversation"
    else:
        kol.status = "sent"
    db.commit()
    db.refresh(message)

    if direction == "inbound":
        background_tasks.add_task(analyze_inbound_message, message.id)

    return {
        "status": "accepted",
        "direction": direction,
        "thread_id": thread.id,
        "message_id": message.id,
    }
