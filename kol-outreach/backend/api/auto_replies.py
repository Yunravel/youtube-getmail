"""Template management and operator controls for durable auto replies."""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from db import get_db
from models import AutoReplyTemplate, MailboxCredential, Message, ScheduledReply, Thread
from services.auto_reply import ensure_global_template
from services.quote_detection import detect_quote, quote_summary

router = APIRouter()


class TemplateIn(BaseModel):
    campaign_id: Optional[str] = None
    campaign_name: Optional[str] = None
    enabled: bool = False
    subject_template: str
    body_template: str
    campaign_context: str = ""
    ai_instructions: str = ""
    signature: str = "Partnership Team"


class AutoReplySettingIn(BaseModel):
    enabled: bool


class PreviewIn(TemplateIn):
    message_id: Optional[int] = None


class DraftUpdateIn(BaseModel):
    subject: str
    body: str


class ScheduleUpdateIn(BaseModel):
    scheduled_at: datetime


class ManualReplyIn(BaseModel):
    subject: str
    body: str
    scheduled_at: Optional[datetime] = None
    send_now: bool = False


def _template_out(item: AutoReplyTemplate) -> dict:
    return {
        "id": item.id,
        "scope_key": item.scope_key,
        "campaign_id": item.campaign_id,
        "campaign_name": item.campaign_name,
        "enabled": item.enabled,
        "auto_send_enabled": item.auto_send_enabled,
        "subject_template": item.subject_template,
        "body_template": item.body_template,
        "campaign_context": item.campaign_context or "",
        "ai_instructions": item.ai_instructions or "",
        "signature": item.signature,
        "version": item.version,
        "updated_at": item.updated_at,
    }


def _task_out(task: ScheduledReply) -> dict:
    return {
        "id": task.id,
        "thread_id": task.thread_id,
        "source_message_id": task.source_message_id,
        "status": task.status,
        "quote": task.quote_snapshot or {},
        "draft_subject": task.draft_subject,
        "draft_body": task.draft_body,
        "scheduled_at": task.scheduled_at,
        "deadline_at": task.deadline_at,
        "retry_count": task.retry_count,
        "manual_override": task.manual_override,
        "error_message": task.error_message,
        "edited_at": task.edited_at,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


@router.get("/templates")
def list_templates(db: Session = Depends(get_db)):
    ensure_global_template(db)
    db.commit()
    items = db.query(AutoReplyTemplate).order_by(AutoReplyTemplate.scope_key).all()
    campaigns = db.query(Thread.campaign_id, Thread.campaign_name).filter(
        Thread.campaign_id.isnot(None)
    ).distinct().order_by(Thread.campaign_name, Thread.campaign_id).all()
    return {
        "templates": [_template_out(item) for item in items],
        "campaigns": [{"id": cid, "name": name or cid} for cid, name in campaigns],
    }


@router.put("/templates")
def upsert_template(body: TemplateIn, db: Session = Depends(get_db)):
    campaign_id = (body.campaign_id or "").strip() or None
    scope_key = f"campaign:{campaign_id}" if campaign_id else "__global__"
    item = db.query(AutoReplyTemplate).filter(AutoReplyTemplate.scope_key == scope_key).first()
    if not item:
        item = AutoReplyTemplate(scope_key=scope_key, campaign_id=campaign_id)
        db.add(item)
    item.campaign_name = body.campaign_name or ("全局默认" if not campaign_id else campaign_id)
    item.enabled = body.enabled
    item.subject_template = body.subject_template.strip()
    item.body_template = body.body_template.strip()
    item.campaign_context = body.campaign_context.strip()
    item.ai_instructions = body.ai_instructions.strip()
    item.signature = body.signature.strip() or "Partnership Team"
    item.version = (item.version or 0) + 1
    if not item.subject_template or not item.body_template:
        raise HTTPException(422, "主题模板和正文模板不能为空")
    db.commit()
    db.refresh(item)
    return _template_out(item)


@router.put("/settings")
def update_settings(
    body: AutoReplySettingIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    global_template = ensure_global_template(db)
    if body.enabled:
        if not global_template.enabled:
            raise HTTPException(409, "请先启用全局默认模板")
        verified = db.query(MailboxCredential).filter(
            MailboxCredential.enabled.is_(True),
            MailboxCredential.smtp_verified_at.isnot(None),
        ).count()
        if not verified:
            raise HTTPException(409, "至少需要一个启用且通过 SMTP 测试的邮箱")
    global_template.auto_send_enabled = body.enabled
    db.commit()
    if body.enabled:
        from datetime import timedelta
        from services.auto_reply import evaluate_message_for_auto_reply
        recent_ids = [row[0] for row in db.query(Message.id).filter(
            Message.direction == "inbound",
            Message.received_at >= datetime.utcnow() - timedelta(hours=2),
        ).all()]
        for message_id in recent_ids:
            background_tasks.add_task(evaluate_message_for_auto_reply, message_id)
    return {"enabled": global_template.auto_send_enabled}


@router.post("/templates/preview")
def preview_template(body: PreviewIn, db: Session = Depends(get_db)):
    message = db.get(Message, body.message_id) if body.message_id else None
    creator = message.thread.kol.name if message and message.thread and message.thread.kol else "Creator"
    original = message.subject if message else "Collaboration opportunity"
    quote = detect_quote(message.body_text or "", message.attachments or []) if message else {
        "items": [{"currency": "USD", "amount": 500, "evidence": "Dedicated video — USD 500"}]
    }
    values = {
        "creator_name": creator,
        "campaign_name": body.campaign_name or "Campaign",
        "original_subject": original.removeprefix("Re: "),
        "quote_summary": quote_summary(quote),
        "question_response": "We've noted your questions and will address them in our follow-up.",
        "sender_name": body.signature,
        "sender_email": "sender@example.com",
        "signature": body.signature,
    }
    import re
    render = lambda text: re.sub(r"{{\s*([a-z_]+)\s*}}", lambda m: values.get(m.group(1), ""), text)
    return {"subject": render(body.subject_template), "body": render(body.body_template), "quote": quote}


@router.get("/threads/{thread_id}/task")
def get_thread_task(thread_id: int, db: Session = Depends(get_db)):
    task = db.query(ScheduledReply).filter(
        ScheduledReply.thread_id == thread_id
    ).order_by(ScheduledReply.created_at.desc(), ScheduledReply.id.desc()).first()
    return _task_out(task) if task else None


@router.post("/threads/{thread_id}/manual")
def create_manual_reply(
    thread_id: int,
    body: ManualReplyIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Create an operator-authored reply without requiring quote detection."""
    subject = body.subject.strip()
    content = body.body.strip()
    if not subject or not content:
        raise HTTPException(422, "主题和正文不能为空")
    thread = db.get(Thread, thread_id)
    if not thread:
        raise HTTPException(404, "会话不存在")
    latest = db.query(Message).filter(Message.thread_id == thread_id).order_by(
        Message.received_at.desc(), Message.id.desc()
    ).first()
    if not latest or latest.direction != "inbound":
        raise HTTPException(409, "当前会话没有可回复的最新入站邮件，或已有更新的发出邮件")
    if not latest.rfc_message_id:
        raise HTTPException(409, "最新入站邮件尚未同步 RFC Message-ID，暂时无法安全回复")
    recipient = (latest.to_email or "").strip().lower()
    if not recipient or recipient.endswith("@snov.local") or recipient.endswith("@provider.local"):
        raise HTTPException(409, "无法识别这封邮件的原收件邮箱，请先在邮箱配置页重新同步历史邮件")
    credential = db.query(MailboxCredential).filter(
        MailboxCredential.email.ilike(recipient),
        MailboxCredential.enabled.is_(True),
    ).first()
    if not credential:
        raise HTTPException(409, f"原收件邮箱 {recipient} 未配置或未启用")
    if not credential.smtp_verified_at:
        raise HTTPException(409, f"原收件邮箱 {recipient} 尚未通过 SMTP 测试")

    task = db.query(ScheduledReply).filter(
        ScheduledReply.source_message_id == latest.id
    ).first()
    if task and task.status in {"sent", "sending"}:
        raise HTTPException(409, "这封入站邮件已有正在发送或已发送的回复")
    if not task:
        task = ScheduledReply(thread_id=thread_id, source_message_id=latest.id)
        db.add(task)

    now = datetime.utcnow()
    scheduled_at = body.scheduled_at
    if scheduled_at and scheduled_at.tzinfo is not None:
        scheduled_at = scheduled_at.astimezone(timezone.utc).replace(tzinfo=None)
    if body.send_now:
        scheduled_at = now
    elif not scheduled_at or scheduled_at <= now:
        raise HTTPException(422, "指定发送时间必须晚于当前时间")
    if scheduled_at > now + timedelta(days=30):
        raise HTTPException(422, "手动定时发送最多可安排到 30 天内")

    task.status = "queued"
    task.mailbox_credential_id = credential.id
    task.quote_snapshot = {"manual": True, "confirmed": False, "items": []}
    task.template_snapshot = None
    task.template_id = None
    task.draft_subject = subject
    task.draft_body = content
    task.scheduled_at = scheduled_at
    task.deadline_at = None
    task.manual_override = True
    task.error_message = None
    task.edited_at = now
    db.commit()
    db.refresh(task)
    if body.send_now:
        from services.auto_reply import send_due_task
        background_tasks.add_task(send_due_task, task.id)
    return _task_out(task)


@router.patch("/tasks/{task_id}")
def update_draft(task_id: int, body: DraftUpdateIn, db: Session = Depends(get_db)):
    task = db.get(ScheduledReply, task_id)
    if not task:
        raise HTTPException(404, "自动回复任务不存在")
    if task.status != "queued":
        raise HTTPException(409, "只有已排队且尚未发送的草稿可以编辑")
    if not body.subject.strip() or not body.body.strip():
        raise HTTPException(422, "主题和正文不能为空")
    task.draft_subject = body.subject.strip()
    task.draft_body = body.body.strip()
    task.edited_at = datetime.utcnow()
    db.commit()
    db.refresh(task)
    return _task_out(task)


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(ScheduledReply, task_id)
    if not task:
        raise HTTPException(404, "自动回复任务不存在")
    if task.status not in {"queued", "awaiting_attachment"}:
        raise HTTPException(409, "该任务当前不能取消")
    task.status = "cancelled"
    task.error_message = "运营手动取消"
    db.commit()
    return _task_out(task)


@router.post("/tasks/{task_id}/schedule")
def schedule_task(task_id: int, body: ScheduleUpdateIn, db: Session = Depends(get_db)):
    task = db.get(ScheduledReply, task_id)
    if not task:
        raise HTTPException(404, "自动回复任务不存在")
    if task.status != "queued":
        raise HTTPException(409, "只有已排队且尚未发送的任务可以指定发送时间")
    scheduled_at = body.scheduled_at
    if scheduled_at.tzinfo is not None:
        scheduled_at = scheduled_at.astimezone(timezone.utc).replace(tzinfo=None)
    now = datetime.utcnow()
    if scheduled_at <= now:
        raise HTTPException(422, "指定发送时间必须晚于当前时间")
    if scheduled_at > now + timedelta(days=30):
        raise HTTPException(422, "手动定时发送最多可安排到 30 天内")
    task.scheduled_at = scheduled_at
    task.manual_override = True
    task.error_message = None
    db.commit()
    db.refresh(task)
    return _task_out(task)


@router.post("/tasks/{task_id}/send-now")
def send_task_now(
    task_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    task = db.get(ScheduledReply, task_id)
    if not task:
        raise HTTPException(404, "自动回复任务不存在")
    if task.status != "queued":
        raise HTTPException(409, "只有已排队且尚未发送的任务可以立即发送")
    task.scheduled_at = datetime.utcnow()
    task.manual_override = True
    task.error_message = None
    db.commit()
    from services.auto_reply import send_due_task
    background_tasks.add_task(send_due_task, task.id)
    db.refresh(task)
    return _task_out(task)
