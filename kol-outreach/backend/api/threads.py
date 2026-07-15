"""会话接口 - Hot Lead 看板 / 分配 / 详情 / 关闭"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from db import get_db
from models import Thread, Message, Note, Operator

router = APIRouter()


class ThreadOut(BaseModel):
    id: int
    kol_id: int
    kol_name: Optional[str] = None
    kol_email: Optional[str] = None
    kol_channel_url: Optional[str] = None
    subject: Optional[str] = None
    campaign_id: Optional[str] = None
    campaign_name: Optional[str] = None
    status: str
    assignee_id: Optional[int] = None
    assignee_name: Optional[str] = None
    last_intent: Optional[str] = None
    intent_score: int = 0
    ai_summary: Optional[str] = None
    reply_count: int = 0
    last_message_preview: Optional[str] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


@router.get("", response_model=list[ThreadOut])
def list_threads(
    status: Optional[str] = None,
    campaign_id: Optional[str] = None,
    assignee_id: Optional[int] = None,
    unassigned_only: bool = False,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """
    会话列表 - Hot Lead 看板主数据源
    默认按 intent_score 降序(最热的排最前)
    """
    q = db.query(Thread)
    if status:
        q = q.filter(Thread.status == status)
    if campaign_id:
        q = q.filter(Thread.campaign_id == campaign_id)
    if assignee_id:
        q = q.filter(Thread.assignee_id == assignee_id)
    if unassigned_only:
        q = q.filter(Thread.assignee_id.is_(None))

    # Hot 的优先,然后按分数降序,再按更新时间
    q = q.order_by(
        (Thread.status == "hot").desc(),
        Thread.intent_score.desc(),
        Thread.updated_at.desc(),
    )
    threads = q.offset((page - 1) * size).limit(size).all()

    # 补充字段(避免 N+1,这里数据量小先简单处理)
    result = []
    for t in threads:
        last_msg = db.query(Message).filter(
            Message.thread_id == t.id,
            Message.direction == "inbound",
        ).order_by(Message.received_at.desc()).first()

        result.append(ThreadOut(
            id=t.id,
            kol_id=t.kol_id,
            kol_name=t.kol.name if t.kol else None,
            kol_email=t.kol.email if t.kol else None,
            kol_channel_url=t.kol.channel_url if t.kol else None,
            subject=t.subject,
            campaign_id=t.campaign_id,
            campaign_name=t.campaign_name,
            status=t.status,
            assignee_id=t.assignee_id,
            assignee_name=t.assignee.name if t.assignee else None,
            last_intent=t.last_intent,
            intent_score=t.intent_score or 0,
            ai_summary=t.ai_summary,
            reply_count=t.reply_count,
            last_message_preview=(last_msg.body_text[:120] if last_msg and last_msg.body_text else None),
            updated_at=t.updated_at,
        ))
    return result


@router.get("/{thread_id}")
def get_thread_detail(thread_id: int, db: Session = Depends(get_db)):
    """会话详情 - 邮件往来 + AI 分析 + 备注"""
    thread = db.query(Thread).get(thread_id)
    if not thread:
        raise HTTPException(404, "Thread not found")

    messages = db.query(Message).filter(
        Message.thread_id == thread_id
    ).order_by(Message.received_at).all()

    notes = db.query(Note).filter(
        Note.thread_id == thread_id
    ).order_by(Note.created_at).all()

    return {
        "thread": {
            "id": thread.id,
            "kol_id": thread.kol_id,
            "kol_name": thread.kol.name if thread.kol else None,
            "kol_email": thread.kol.email if thread.kol else None,
            "subject": thread.subject,
            "campaign_id": thread.campaign_id,
            "campaign_name": thread.campaign_name,
            "status": thread.status,
            "assignee_id": thread.assignee_id,
            "last_intent": thread.last_intent,
            "intent_score": thread.intent_score,
            "ai_summary": thread.ai_summary,
            "reply_count": thread.reply_count,
            "updated_at": thread.updated_at,
        },
        "messages": [
            {
                "id": m.id,
                "direction": m.direction,
                "from_email": m.from_email,
                "to_email": m.to_email,
                "subject": m.subject,
                "body_text": m.body_text,
                "attachments": m.attachments or [],
                "ai_analysis": m.ai_analysis,
                "is_read": m.is_read,
                "received_at": m.received_at,
            }
            for m in messages
        ],
        "notes": [
            {
                "id": n.id,
                "operator_name": n.operator.name if n.operator else None,
                "content": n.content,
                "created_at": n.created_at,
            }
            for n in notes
        ],
    }


class AssignIn(BaseModel):
    assignee_id: int


@router.post("/{thread_id}/assign")
def assign_thread(thread_id: int, body: AssignIn, db: Session = Depends(get_db)):
    """分配给运营(接管)"""
    thread = db.query(Thread).get(thread_id)
    if not thread:
        raise HTTPException(404, "Thread not found")
    operator = db.query(Operator).get(body.assignee_id)
    if not operator or not operator.is_active:
        raise HTTPException(422, "Operator not found or inactive")
    thread.assignee_id = body.assignee_id
    db.commit()
    return {"thread_id": thread_id, "assignee_id": body.assignee_id}


class StatusIn(BaseModel):
    status: str  # open/hot/warming/cooling/closed


@router.post("/{thread_id}/status")
def update_status(thread_id: int, body: StatusIn, db: Session = Depends(get_db)):
    """更新会话状态(关闭/重新打开)"""
    thread = db.query(Thread).get(thread_id)
    if not thread:
        raise HTTPException(404, "Thread not found")
    allowed_statuses = {"open", "hot", "warming", "cooling", "closed"}
    if body.status not in allowed_statuses:
        raise HTTPException(422, "Invalid thread status")
    thread.status = body.status
    db.commit()
    return {"thread_id": thread_id, "status": body.status}


class NoteIn(BaseModel):
    operator_id: int
    content: str


@router.post("/{thread_id}/notes")
def add_note(thread_id: int, body: NoteIn, db: Session = Depends(get_db)):
    """添加内部备注"""
    thread = db.query(Thread).get(thread_id)
    if not thread:
        raise HTTPException(404, "Thread not found")
    operator = db.query(Operator).get(body.operator_id)
    if not operator or not operator.is_active:
        raise HTTPException(422, "Operator not found or inactive")
    note = Note(thread_id=thread_id, operator_id=body.operator_id, content=body.content)
    db.add(note)
    db.commit()
    return {"id": note.id}
