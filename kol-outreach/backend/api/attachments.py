"""附件同步触发 + 下载 API。

- POST /attachments/sync：手动触发同步（限定时间范围），后台异步跑，返回 job_id
- GET /attachments/sync/status：查手动任务进度
- GET /attachments/download/{message_id}/{filename}：下载已存盘的附件
"""
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from urllib.parse import quote

from config import settings
from db import get_db
from models import Message

router = APIRouter()


# ===== 手动同步后台任务的内存状态 =====
# 同 threads.py 的 _backfill_jobs 模式；同时只允许一个同步任务在跑。
_sync_jobs: dict[str, dict] = {}


def _run_sync_job(job_id: str, since_days: Optional[int]) -> None:
    """后台同步任务函数。"""
    from services.attachment_sync import sync_all_mailboxes

    job = _sync_jobs[job_id]
    try:
        result = sync_all_mailboxes(
            mode="manual",
            since_days=since_days,
            on_mailbox_progress=lambda done, total, email: job.update(
                {"processed": done, "total": total, "current_email": email}
            ),
        )
        job["status"] = "done"
        job["result"] = result
        job["finished_at"] = datetime.utcnow().isoformat()
    except Exception as e:
        job["status"] = "failed"
        job["error"] = str(e)
        job["finished_at"] = datetime.utcnow().isoformat()


class SyncIn(BaseModel):
    since_days: Optional[int] = 30   # None 或 0 表示全部历史


@router.post("/sync")
def trigger_sync(body: SyncIn, background_tasks: BackgroundTasks):
    """手动触发附件同步。since_days=None/0 表示全部；默认最近 30 天。

    返回 job_id，前端用 GET /attachments/sync/status 轮询。
    """
    # 同时只允许一个任务
    running = [j for j in _sync_jobs.values() if j.get("status") == "running"]
    if running:
        raise HTTPException(409, "已有同步任务在跑，请等它完成")

    since = body.since_days if (body.since_days and body.since_days > 0) else None
    job_id = str(uuid.uuid4())[:8]
    _sync_jobs[job_id] = {
        "status": "running",
        "since_days": since,
        "started_at": datetime.utcnow().isoformat(),
        "processed": 0,
        "total": 0,
        "current_email": None,
    }
    background_tasks.add_task(_run_sync_job, job_id, since)
    return {"job_id": job_id, "status": "running"}


@router.get("/sync/status")
def sync_status(job_id: Optional[str] = None):
    """查同步进度。传 job_id 查指定任务；不传查当前/最近一个。"""
    if not _sync_jobs:
        return {"jobs": [], "current": None}
    if job_id:
        if job_id not in _sync_jobs:
            raise HTTPException(404, "任务不存在")
        return {"job_id": job_id, **_sync_jobs[job_id]}
    # 返回最近一个
    latest_id = next(reversed(_sync_jobs))
    return {"job_id": latest_id, **_sync_jobs[latest_id], "jobs": list(_sync_jobs.keys())}


# ===== 附件下载 =====
# 文件名只允许字母数字点下划线连字符中文，防任何路径穿越。
_SAFE_FILENAME_RE = re.compile(r"^[\w.\u4e00-\u9fff \-()]+$")


@router.get("/download/{message_id}/{filename}")
def download_attachment(message_id: int, filename: str, db=Depends(get_db)):
    """下载已存盘的附件。校验 message 存在 + 文件名安全 + 路径不越界。"""
    # 1. message 必须存在
    message = db.get(Message, message_id)
    if not message:
        raise HTTPException(404, "邮件不存在")

    # 2. 文件名安全检查
    if not filename or not _SAFE_FILENAME_RE.match(filename) or ".." in filename:
        raise HTTPException(400, "非法文件名")

    # 3. 校验该文件确属此 message 的附件（从 attachments 元数据里查）
    attachments = message.attachments or []
    matched_meta = None
    for att in attachments:
        local_path = att.get("local_path") if isinstance(att, dict) else None
        if local_path == f"{message_id}/{filename}":
            matched_meta = att
            break
    if not matched_meta:
        raise HTTPException(404, "附件不存在或未授权")

    # 4. 拼绝对路径并校验不越界（双保险）
    storage_root = settings.attachment_storage_path.resolve()
    file_path = (storage_root / str(message_id) / filename).resolve()
    try:
        file_path.relative_to(storage_root)
    except ValueError:
        raise HTTPException(400, "非法路径")
    if not file_path.is_file():
        raise HTTPException(404, "附件文件已丢失")

    # 5. 流式返回（Content-Disposition 用 RFC 5987 支持中文文件名）
    data = file_path.read_bytes()
    content_type = matched_meta.get("content_type") or "application/octet-stream"
    ascii_name = filename.encode("ascii", "ignore").decode() or "attachment"
    disposition = (
        f"attachment; filename=\"{ascii_name}\"; "
        f"filename*=UTF-8''{quote(filename)}"
    )
    import io
    return StreamingResponse(
        io.BytesIO(data),
        media_type=content_type,
        headers={"Content-Disposition": disposition},
    )
