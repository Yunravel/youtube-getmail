"""会话接口 - Hot Lead 看板 / 分配 / 详情 / 关闭 / 导出"""
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from db import get_db
from models import Thread, Message, Note, Operator

router = APIRouter()


# ===== AI 回填后台任务的内存状态（重启丢失可接受，任务幂等可重跑）=====
# 同时只允许一个回填任务在跑，避免并发 DeepSeek 调用翻倍。
_backfill_jobs: dict[str, dict] = {}


def _run_backfill_job(job_id: str, thread_ids: Optional[list[int]], force: bool) -> None:
    """后台任务函数：被 BackgroundTasks 调度，更新内存 job 状态。"""
    from scripts.backfill_kol_profile import run_backfill

    job = _backfill_jobs[job_id]
    try:
        def on_progress(done, total):
            job["processed"] = done
            job["total"] = total

        result = run_backfill(
            thread_ids=thread_ids,
            commit=True,
            force=force,
            workers=4,
            on_progress=on_progress,
        )
        job["status"] = "done"
        job["result"] = result
        job["finished_at"] = datetime.utcnow().isoformat()
    except Exception as e:
        job["status"] = "failed"
        job["error"] = str(e)
        job["finished_at"] = datetime.utcnow().isoformat()


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


# ===== AI 画像回填 + 报价导出（HotLead 看板扩展）=====
# 注意：字面量路由必须定义在 /{thread_id} 之前，否则会被 {thread_id} 抢先匹配。


class BackfillProfileIn(BaseModel):
    thread_ids: Optional[list[int]] = None  # 空 → 处理全部 hot
    force: bool = False                      # True 覆盖非空字段


@router.post("/backfill-profile")
def backfill_profile(
    body: BackfillProfileIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """触发 AI 画像回填后台任务。

    从每个 thread 最新一封回信正文里，AI 提取平台/账号/粉丝/画像/报价/合作条件，
    回填到 kol 表与 message.ai_analysis（增量，默认只填空）。
    立即返回 job_id，前端轮询 /backfill-status 看进度。
    同时只允许一个回填任务在跑。
    """
    running = [j for j in _backfill_jobs.values() if j["status"] == "running"]
    if running:
        raise HTTPException(409, f"已有回填任务在跑: {running[0]['job_id']}")

    thread_ids = body.thread_ids
    if not thread_ids:
        thread_ids = [t.id for t in db.query(Thread).filter(Thread.status == "hot").all()]
    if not thread_ids:
        return {"job_id": None, "total": 0, "message": "没有符合条件的会话"}

    job_id = str(uuid.uuid4())[:8]
    _backfill_jobs[job_id] = {
        "job_id": job_id,
        "status": "running",
        "total": len(thread_ids),
        "processed": 0,
        "force": body.force,
        "started_at": datetime.utcnow().isoformat(),
    }
    background_tasks.add_task(_run_backfill_job, job_id, thread_ids, body.force)
    return {"job_id": job_id, "total": len(thread_ids)}


@router.get("/backfill-status")
def backfill_status():
    """查最新回填任务进度。返回最近一个 job 的状态。"""
    if not _backfill_jobs:
        return {"job_id": None, "status": "idle"}
    latest = list(_backfill_jobs.values())[-1]
    return latest


class ExportIn(BaseModel):
    thread_ids: list[int]
    project: str = "dola_uk"          # dola_uk / pippit_2026


@router.post("/export")
def export_threads(body: ExportIn, db: Session = Depends(get_db)):
    """按规范导出勾选 thread 的报价单 Excel。"""
    if not body.thread_ids:
        raise HTTPException(422, "thread_ids 不能为空")
    if len(body.thread_ids) > 200:
        raise HTTPException(422, "单次最多导出 200 条")

    from services.export_quote import build_quote_workbook

    project_code = "pippit_2026" if "pippit" in body.project.lower() else "dola_uk"
    xlsx_bytes = build_quote_workbook(db, body.thread_ids, project_code=project_code)

    import io
    from urllib.parse import quote
    datestr = datetime.utcnow().strftime('%Y%m%d')
    # Content-Disposition 头不支持非 ASCII；用 RFC 5987 的 filename* 给中文，
    # 同时给个纯 ASCII 的 filename 兜底（部分老客户端只认 filename）。
    cn_name = f"已报价KOL_{len(body.thread_ids)}位_{datestr}.xlsx"
    ascii_name = f"quoted_kol_{len(body.thread_ids)}_{datestr}.xlsx"
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(cn_name)}"
    return StreamingResponse(
        io.BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": disposition},
    )


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
