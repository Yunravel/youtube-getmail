"""Snov 集成管理接口（受看板登录保护）。"""
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator
from sqlalchemy import or_
from sqlalchemy.orm import Session

from db import SessionLocal, get_db
from models import Kol, Message, Thread
from config import settings
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
        raise HTTPException(502, f"营销平台客户端初始化失败: {exc}")


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
        raise HTTPException(502, f"营销平台连接失败: {exc}")


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
        raise HTTPException(502, f"获取营销活动失败: {exc}")


@router.get("/webhooks")
def list_webhooks():
    try:
        return get_snov_client().list_webhooks()
    except Exception as exc:
        raise HTTPException(502, f"获取营销平台通知配置失败: {exc}")


@router.post("/sync-contacts")
def sync_contacts(db: Session = Depends(get_db)):
    """Import every current Snov prospect list into the local contact table."""
    try:
        return sync_snov_contacts(db, get_snov_client())
    except Exception as exc:
        raise HTTPException(502, f"同步营销平台联系人失败: {exc}")


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
        raise HTTPException(502, f"创建待发送名单失败: {exc}")


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
        raise HTTPException(502, f"创建营销平台通知配置失败: {exc}")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _parse_snov_time(value: Any) -> tuple[datetime, bool]:
    """把 Snov 的 receivedAt 转为数据库使用的 UTC naive datetime。

    返回 ``(received_at, estimated)``。estimated=True 表示 Snov 没给时间或
    值解析不了，received_at 是入库时刻的推算值——历史补拉的旧回信会显示成
    "刚刚"，消费端（自动回复新鲜度闸门）必须据此拒绝自动放行。

    幂等键红线：``_historical_message_id`` 的指纹用的是原始 ``received_value``
    字符串，本函数输出只进 received_at 列，两者互不影响。
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.utcfromtimestamp(value), False
        except (OverflowError, OSError, ValueError):
            pass
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
        else:
            if parsed.tzinfo is not None:
                # 带偏移 → 换算到 UTC 再去 tzinfo；旧实现直接丢偏移导致
                # +03:00 的 15 点被当成 UTC 15 点（实际 UTC 12 点）。
                return parsed.astimezone(timezone.utc).replace(tzinfo=None), False
            # 无偏移的 naive 串按 UTC 解释（Snov 的时间基准是 UTC）。
            return parsed, False
    return datetime.utcnow(), True


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


def _enqueue_kol_enrich(kol_ids: list[int]) -> None:
    """把回信 KOL 的画像补全丢线程池异步跑（不阻塞 sync-replies 响应）。

    单独成函数，便于测试 monkeypatch 为 no-op，避免后台线程访问已销毁的
    内存测试库（no such table）。
    """
    from concurrent.futures import ThreadPoolExecutor
    from services.kol_enrich import enrich_reply_kol

    pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="kol-enrich")
    for kid in kol_ids:
        pool.submit(enrich_reply_kol, kid)
    pool.shutdown(wait=False)
    logger.info("已排队 %d 个回信 KOL 的画像补全任务", len(kol_ids))


def _enqueue_feishu_push(message_ids: list[int]) -> None:
    """Persist Feishu tasks; the scheduler performs retryable delivery."""
    from services.feishu_push import enqueue_messages_sync
    from services.quote_source_analysis import analyze_and_save_quote_sources

    # Historical sync starts profile enrichment in a separate worker.  A short
    # due-time delay allows that enrichment to commit before delivery.
    for message_id in message_ids:
        analyze_and_save_quote_sources(message_id)
    queued = enqueue_messages_sync(list(message_ids), delay_seconds=120)
    logger.info("已持久化 %d 个飞书同步任务", queued)


@router.post("/sync-replies")
def sync_historical_replies(db: Session = Depends(get_db)):
    """补拉 Snov Campaign 的历史回信；可安全重复执行，不会创建重复消息。

    三阶段设计（DATABASE_DEVELOPMENT.md §8，外部 API 不在 DB 事务内）：
      阶段 1：Snov HTTP 拉取全部回信到内存（不碰 db，session 不持有事务/连接）
      阶段 2：单事务落库（查重→建 KOL/Thread/Message→supersede），commit
      阶段 3：commit 后用独立 session 批量跑 analyze_intent 补写 AI 结果
    AI 分析失败不影响回信入库（回信是业务核心，AI 是增强）。
    """
    # ==================== 阶段 1：Snov 拉取（纯 HTTP，不用 db）====================
    # 注意：Depends(get_db) 的 session 对象此刻已开，但在执行首条 SQL 前不会
    # 借连接/开事务（SQLAlchemy 惰性连接）。本阶段不调用任何 db 方法，故 Snov
    # HTTP 期间无未提交事务、无锁。
    try:
        client = get_snov_client()
        campaigns = client.list_campaigns()
    except Exception as exc:
        raise HTTPException(502, f"获取营销活动失败: {exc}")

    result = {
        "campaigns": len(campaigns),
        "created_kols": 0,
        "created_threads": 0,
        "created_messages": 0,
        "duplicates": 0,
        "skipped": 0,
        "errors": [],
    }

    # 把所有 campaign 的回信一次性拉到内存，供阶段 2 落库。
    reply_batches: list[tuple[str, str, list]] = []
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
        reply_batches.append((campaign_id, campaign_name, records))

    # ==================== 阶段 2：落库（首次 SQL 触发事务，无 AI）====================
    created_message_ids: list[int] = []
    created_kol_ids: list[int] = []
    analysis_jobs: list[dict] = []  # 收集待 AI 分析任务，commit 前把 ORM 属性读出来
    # 同一 KOL 先后发多封内容完全相同的回信（且 Snov 未给 receivedAt）会产生
    # 相同指纹：按批内出现次序追加序号，避免第二封被误判为 duplicate 而丢失。
    fingerprint_counts: dict[str, int] = {}

    try:
        for campaign_id, campaign_name, records in reply_batches:
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
                    ) or "历史回信"
                    raw_body = _text(
                        reply.get("message")
                        or reply.get("body")
                        or reply.get("bodyText")
                        or reply.get("body_text")
                        or reply.get("emailBody")
                        or reply.get("email_body")
                    )
                    body = clean_email_body(raw_body) or "[历史回信；正文为空]"
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
                    received_at, received_at_estimated = _parse_snov_time(received_value)
                    base_message_id = _historical_message_id(
                        campaign_id,
                        prospect_email,
                        subject,
                        body,
                        received_value,
                    )
                    # 批内第 N 次出现同一指纹 → 追加序号，保证每封都有独立幂等键。
                    occurrence = fingerprint_counts.get(base_message_id, 0)
                    fingerprint_counts[base_message_id] = occurrence + 1
                    message_id = (
                        base_message_id
                        if occurrence == 0
                        else f"{base_message_id}#{occurrence}"
                    )
                    # Snov 的 prospectId 每次读取都会变化，不能用于幂等键。旧版
                    # 导入过的数据则通过邮件内容与接收时间识别，兼容一次性补数据。
                    existing_message = db.query(Message).filter(
                        Message.message_id == message_id
                    ).first()
                    if not existing_message:
                        content_matches = (
                            db.query(Message)
                            .filter(
                                Message.direction == "inbound",
                                Message.from_email == prospect_email,
                                Message.subject == subject,
                                Message.body_text == body,
                                Message.received_at == received_at,
                            )
                            .order_by(Message.id.asc())
                            .all()
                        )
                        # 已入库的同内容消息数（可能来自 webhook 或旧版导入）多于
                        # 本批中该指纹的出现序号时，说明"这一封"已存在。
                        if len(content_matches) > occurrence:
                            existing_message = content_matches[occurrence]
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
                        created_kol_ids.append(kol.id)

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
                        received_at_estimated=received_at_estimated,
                    )
                    db.add(message)
                    db.flush()
                    created_message_ids.append(message.id)
                    from services.auto_reply import supersede_thread_tasks
                    supersede_thread_tasks(db, thread.id, message.id)
                    thread.reply_count = (thread.reply_count or 0) + 1
                    kol.status = "in_conversation"

                    # 收集待分析任务：在 commit 前（session 活跃）把 ORM 属性读出来，
                    # 阶段 3 会用独立 session 写 AI 结果，届时这些 ORM 对象已 detach。
                    analysis_jobs.append({
                        "message_id": message.id,
                        "thread_id": thread.id,
                        "body": body,
                        "kol_name": kol.name,
                        "subject": subject,
                    })
                    result["created_messages"] += 1

        db.commit()
    except Exception:
        db.rollback()
        raise

    # ==================== 阶段 3：AI 分析（commit 后，独立 session）====================
    # analyze_intent 内部自管 DeepSeek/OpenAI HTTP，完全脱离 DB session。
    # 用独立短事务写结果；AI 失败只丢分析，不影响已 commit 的回信。
    _run_intent_analysis(analysis_jobs)

    # ==================== 阶段 4：后处理（自管 session，在 commit 后）====================
    from services.auto_reply import evaluate_message_for_auto_reply
    for created_message_id in created_message_ids:
        evaluate_message_for_auto_reply(created_message_id)
    # 新建的回信 KOL 无频道画像，丢线程池异步补全（不阻塞本响应）。
    # 抽到模块级函数，便于测试置空避免跨线程访问已销毁的测试库。
    if created_kol_ids:
        _enqueue_kol_enrich(created_kol_ids)
    # 每条新回信推送到飞书多维表格（未配置时内部静默跳过）。
    if created_message_ids:
        _enqueue_feishu_push(created_message_ids)
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

    return result


def _run_intent_analysis(jobs: list[dict]) -> None:
    """批量对新建回信跑意向分析并写回 message/thread（独立短事务，在落库 commit 后）。

    遵循 §8：analyze_intent 的外部 LLM HTTP 不在任何 DB session 内。失败不抛出，
    仅记录日志——回信已入库，AI 结果缺失不影响主流程，下次重跑可补。
    """
    if not jobs:
        return
    db = SessionLocal()
    try:
        for job in jobs:
            analysis = analyze_intent(
                job["body"], kol_name=job["kol_name"], subject=job["subject"]
            )
            if not analysis:
                continue
            message = db.get(Message, job["message_id"])
            if not message:
                continue  # 极端情况：回信在 commit 后被删
            message.ai_analysis = analysis
            thread = db.get(Thread, job["thread_id"])
            if thread:
                thread.last_intent = analysis.get("intent")
                thread.intent_score = analysis.get("intent_score", 0)
                thread.ai_summary = analysis.get("summary")
                next_status = intent_to_thread_status(analysis)
                if next_status:
                    thread.status = next_status
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Snov 同步的 AI 分析阶段失败（回信已入库，不影响主流程）")
    finally:
        db.close()


class ReplayToFeishuIn(BaseModel):
    """补推回信到飞书。支持按 message_id 列表，或按最近 N 天的回信。"""

    message_ids: list[int] = Field(default_factory=list)
    days: int = Field(default=0, ge=0, le=365, description="补推最近 N 天的回信（与 message_ids 二选一，默认 7 天）")


@router.post("/replay-to-feishu")
def replay_to_feishu(body: ReplayToFeishuIn, db: Session = Depends(get_db)):
    """把库里已有的回信手动补推到飞书多维表格。

    用途：第一次接入飞书时一次性灌入历史回信；或排查漏推时重试。
    异步排队，立即返回排队数（不等待推送完成）。
    """
    if not settings.feishu_is_configured:
        raise HTTPException(400, "飞书未配置（FEISHU_*），无法推送")

    ids = list(body.message_ids)
    if not ids and body.days == 0:
        days = 7
    else:
        days = body.days
    if not ids:
        since = datetime.utcnow() - timedelta(days=days)
        rows = (
            db.query(Message.id)
            .filter(Message.direction == "inbound")
            .filter(Message.received_at >= since)
            .all()
        )
        ids = [r[0] for r in rows]

    if not ids:
        return {"queued": 0}

    from services.feishu_push import enqueue_messages_sync
    queued = enqueue_messages_sync(ids)
    return {"queued": queued}
