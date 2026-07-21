"""
发信接口 - 通过 Instantly 发送
- /send/reply          运营在会话详情页回复(单封)
- /send/push-campaign  批量推送 KOL 到 campaign(开始序列发送)
- /send/campaigns      列出 Instantly campaign(前端下拉用)
"""
import logging
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from db import get_db
from models import Thread, Kol, SendLog, Message
from services.instantly_client import get_instantly_client

router = APIRouter()
logger = logging.getLogger(__name__)


# ===== 列出 campaign(前端选) =====
@router.get("/campaigns")
def list_campaigns():
    """列出 Instantly 里的所有 campaign"""
    try:
        client = get_instantly_client()
        campaigns = client.list_campaigns()
        return [
            {"id": c.get("id"), "name": c.get("name", "(unnamed)")}
            for c in campaigns
        ]
    except RuntimeError as e:
        raise HTTPException(500, f"Instantly 未配置: {e}")
    except Exception as e:
        raise HTTPException(500, f"获取 campaign 失败: {e}")


# ===== 单封回复 =====
class SendOneIn(BaseModel):
    """运营在会话详情页点回复时用"""
    thread_id: int
    body: str
    operator_id: int
    subject: Optional[str] = None


@router.post("/reply")
def send_reply(body: SendOneIn, db: Session = Depends(get_db)):
    """
    运营回复 - 通过 Instantly 以原发信邮箱身份发出

    注意: 单封"直接回复"Instantly 没有专门的 API,
    实际生产中通常用两种方式之一:
    1. 通过 Gmail/SMTP 直接发(需要存邮箱凭据)
    2. 把回复作为新 lead 的 custom_variable 推到 Instantly(不太适合)

    这里先记录"待发送"日志,真正发信在 Instantly 后台手动发或后续接 SMTP。
    回复内容会存到 message 表(direction=outbound),运营能看到历史。
    """
    thread = db.query(Thread).get(body.thread_id)
    if not thread:
        raise HTTPException(404, "Thread not found")

    kol = thread.kol
    if not kol:
        raise HTTPException(400, "Thread 没有关联 KOL")

    # 存到 message 表(出站记录)
    msg = Message(
        thread_id=thread.id,
        direction="outbound",
        from_email="(via Instantly)",  # 实际发信邮箱由 Instantly 轮发决定
        to_email=kol.email,
        subject=body.subject or f"re: {thread.subject}",
        body_text=body.body,
        received_at=datetime.utcnow(),
    )
    db.add(msg)

    # 发送日志
    log = SendLog(
        thread_id=thread.id,
        kol_id=kol.id,
        provider="instantly",
        status="pending",
    )
    db.add(log)
    db.commit()

    logger.info(f"运营回复已记录 thread={thread.id} kol={kol.email}")
    return {
        "status": "recorded",
        "message_id": msg.id,
        "note": "回复已记录。生产环境接通 Instantly/SMTP 后会真正发出。",
    }


# ===== 批量推送到 campaign =====
class PushToCampaignIn(BaseModel):
    kol_ids: list[int]
    campaign_id: str
    skip_without_intro: bool = True   # 没开场白的跳过?


@router.post("/push-campaign")
def push_to_campaign(body: PushToCampaignIn, db: Session = Depends(get_db)):
    """
    批量推送 KOL 到 Instantly campaign
    - 把 kol.personal_intro 作为 custom_variable 带过去
    - Instantly 接管后续节奏控制(warmup/限流/跟进序列)
    """
    kols = db.query(Kol).filter(Kol.id.in_(body.kol_ids)).all()

    leads_to_send = []
    skipped = []

    for kol in kols:
        # 跳过没邮箱的
        if not kol.email:
            skipped.append({"id": kol.id, "reason": "no_email"})
            continue
        # 跳过没开场白的(如果要求)
        if body.skip_without_intro and not kol.personal_intro:
            skipped.append({"id": kol.id, "reason": "no_intro"})
            continue

        variables = {
            "personal_intro": kol.personal_intro or "",
            "channel_topic": kol.niche or "",
            "kol_name": kol.name,
        }
        # first_name 取名字第一个词
        first_name = kol.name.split()[0] if kol.name else None

        leads_to_send.append({
            "email": kol.email,
            "first_name": first_name,
            "variables": variables,
        })

    if not leads_to_send:
        return {"success": 0, "failed": [], "skipped": skipped, "message": "没有可发送的 KOL"}

    # 调 Instantly 批量推送
    try:
        client = get_instantly_client()
        result = client.add_leads_batch(body.campaign_id, leads_to_send)
    except RuntimeError as e:
        raise HTTPException(500, f"Instantly 未配置: {e}")
    except Exception as e:
        raise HTTPException(500, f"Instantly 推送失败: {e}")

    # 更新 KOL 状态 + 记日志
    for kol in kols:
        if kol.email in [l["email"] for l in leads_to_send]:
            kol.status = "sent"
            log = SendLog(
                kol_id=kol.id,
                provider="instantly",
                provider_campaign_id=body.campaign_id,
                status="sent",
                sent_at=datetime.utcnow(),
            )
            db.add(log)

    db.commit()

    return {
        "success": result["success"],
        "failed": result["failed"],
        "skipped": skipped,
        "total": result["total"],
    }
