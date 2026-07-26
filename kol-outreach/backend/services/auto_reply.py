"""Quote-triggered draft creation, supersession, and durable SMTP delivery."""
from __future__ import annotations

import json
import logging
import random
import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from config import settings
from db import SessionLocal
from models import (
    AutoReplyTemplate, Kol, MailboxCredential, Message, ScheduledReply,
    SendLog, Thread,
)
from models.auto_reply_template import DEFAULT_BODY, DEFAULT_SUBJECT
from services.quote_detection import detect_quote, extract_money_items, quote_summary
from services.smtp_sender import (
    AmbiguousDeliveryError, PreSendError, SmtpCredentialSnapshot,
    build_reply_message, send_message,
)

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = {"awaiting_attachment", "queued", "sending"}
FINAL_STATUSES = {"sent", "cancelled", "superseded", "manual_review", "failed"}
_PLACEHOLDER_RE = re.compile(r"{{\s*([a-z_]+)\s*}}")
_COMMITMENT_RE = re.compile(
    r"\b(we accept|accepted your (?:rate|quote)|deal|confirmed partnership|"
    r"we agree to pay|payment (?:is|has been) approved)\b|"
    r"(接受报价|确认合作|同意支付|付款已批准)", re.I,
)


def ensure_global_template(db: Session) -> AutoReplyTemplate:
    template = db.query(AutoReplyTemplate).filter(AutoReplyTemplate.scope_key == "__global__").first()
    if template:
        return template
    template = AutoReplyTemplate(
        scope_key="__global__",
        campaign_id=None,
        campaign_name="全局默认",
        enabled=False,
        auto_send_enabled=False,
        subject_template=DEFAULT_SUBJECT,
        body_template=DEFAULT_BODY,
    )
    db.add(template)
    db.flush()
    return template


def select_template(db: Session, thread: Thread) -> AutoReplyTemplate | None:
    global_template = ensure_global_template(db)
    if thread.campaign_id:
        campaign = db.query(AutoReplyTemplate).filter(
            AutoReplyTemplate.scope_key == f"campaign:{thread.campaign_id}"
        ).first()
        if campaign:
            return campaign if campaign.enabled else None
    return global_template if global_template.enabled else None


def _template_snapshot(template: AutoReplyTemplate) -> dict[str, Any]:
    return {
        "id": template.id,
        "scope_key": template.scope_key,
        "version": template.version,
        "subject_template": template.subject_template,
        "body_template": template.body_template,
        "campaign_context": template.campaign_context or "",
        "ai_instructions": template.ai_instructions or "",
        "signature": template.signature,
    }


def _safe_original_subject(subject: str) -> str:
    return re.sub(r"^(\s*re\s*:\s*)+", "", subject or "", flags=re.I).strip() or "Collaboration"


def _render(template: str, values: dict[str, str]) -> str:
    return _PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), ""), template or "")


def _generate_question_response(message: Message, template: AutoReplyTemplate) -> str:
    questions = list((message.ai_analysis or {}).get("key_questions") or [])
    if not questions:
        return ""
    if not settings.OPENAI_API_KEY:
        return "We've also noted your questions and will address them in our follow-up."
    try:
        from openai import OpenAI
        options: dict[str, Any] = {"api_key": settings.OPENAI_API_KEY}
        if settings.OPENAI_BASE_URL:
            options["base_url"] = settings.OPENAI_BASE_URL
        client = OpenAI(**options)
        prompt = (
            "Write one short acknowledgement of the creator's questions. Use only facts in "
            "the campaign context. If a fact is unavailable, say it will be addressed in the "
            "follow-up. Do not accept pricing, promise payment, or invent facts.\n"
            f"Campaign context:\n{template.campaign_context or '(none)'}\n"
            f"Additional constraints:\n{template.ai_instructions or '(none)'}\n"
            f"Questions:\n{json.dumps(questions, ensure_ascii=False)}"
        )
        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL_INTENT,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=250,
        )
        return (response.choices[0].message.content or "").strip()
    except Exception:
        logger.exception("自动回复问题回应生成失败 message=%s", message.id)
        return "We've also noted your questions and will address them in our follow-up."


def _validate_draft(body: str, quote: dict[str, Any]) -> str | None:
    if _COMMITMENT_RE.search(body or ""):
        return "草稿包含自动发送禁止的接受报价或付款承诺"
    allowed = {(item["currency"], int(item["amount"])) for item in quote.get("items") or []}
    generated = {(item["currency"], int(item["amount"])) for item in extract_money_items(body or "")}
    if not generated.issubset(allowed):
        return "草稿出现了来源报价中不存在的金额"
    return None


def _upsert_task(db: Session, message: Message, status: str, **values) -> ScheduledReply:
    task = db.query(ScheduledReply).filter(ScheduledReply.source_message_id == message.id).first()
    if not task:
        task = ScheduledReply(thread_id=message.thread_id, source_message_id=message.id, status=status)
        db.add(task)
    elif task.status in FINAL_STATUSES or task.status == "sending":
        # 纵深防御：终态（FINAL_STATUSES）与投递中（sending）的任务不得被重评估
        # 覆写/复活——sent 复活成 queued 会重复外发，cancelled 复活会违背运营取消。
        # 业务级守卫在 evaluate_message_for_auto_reply，这里兜底，原样返回。
        return task
    task.status = status
    for key, value in values.items():
        setattr(task, key, value)
    return task


def supersede_thread_tasks(db: Session, thread_id: int, source_message_id: int | None = None,
                           reason: str = "newer inbound message") -> int:
    query = db.query(ScheduledReply).filter(
        ScheduledReply.thread_id == thread_id,
        ScheduledReply.status.in_(ACTIVE_STATUSES),
    )
    if source_message_id is not None:
        query = query.filter(ScheduledReply.source_message_id != source_message_id)
    tasks = query.all()
    for task in tasks:
        task.status = "superseded"
        task.error_message = reason
    return len(tasks)


def cancel_for_outbound(db: Session, thread_id: int, reason: str = "detected outbound reply") -> int:
    tasks = db.query(ScheduledReply).filter(
        ScheduledReply.thread_id == thread_id,
        ScheduledReply.status.in_(ACTIVE_STATUSES),
    ).all()
    for task in tasks:
        task.status = "cancelled"
        task.error_message = reason
    return len(tasks)


def evaluate_message_for_auto_reply(message_id: int) -> ScheduledReply | None:
    db = SessionLocal()
    try:
        message = db.get(Message, message_id)
        if not message or message.direction != "inbound" or not message.thread:
            return None
        thread = message.thread
        global_template = ensure_global_template(db)
        quote = detect_quote(message.body_text or "", message.attachments or [])
        analysis = dict(message.ai_analysis or {})
        analysis["quote"] = quote
        message.ai_analysis = analysis

        # attachment_sync 会周期性对同一封邮件重跑 evaluate，任务只能在下列前提下被改写：
        # - 终态（FINAL_STATUSES）与投递中（sending）的任务绝不复活/覆写：
        #   sent 复活=重复外发，cancelled 复活=违背运营取消，sending 覆写=双发窗口。
        # - 运营手动接管（edited_at 非空或 manual_override）的任务，draft_subject/
        #   draft_body/scheduled_at/status 以运营为准，模板重渲染不得覆盖。
        # 上面 quote 合并写入 message.ai_analysis 照旧提交，任务本身不动。
        existing_task = db.query(ScheduledReply).filter(
            ScheduledReply.source_message_id == message.id
        ).first()
        if existing_task and (
            existing_task.status in FINAL_STATUSES
            or existing_task.status == "sending"
            or existing_task.manual_override
            or existing_task.edited_at is not None
        ):
            db.commit()
            db.refresh(existing_task)
            return existing_task

        if not global_template.auto_send_enabled:
            db.commit()
            return None
        template = select_template(db, thread)
        if not template:
            db.commit()
            return None
        intent = str((message.ai_analysis or {}).get("intent") or thread.last_intent or "")
        if intent in {"negative", "ooo", "auto", "auto_reply"}:
            db.commit()
            return None

        supersede_thread_tasks(db, thread.id, message.id)
        deadline = (message.received_at or datetime.utcnow()) + timedelta(
            minutes=settings.AUTO_REPLY_MAX_DELAY_MINUTES
        )

        if not quote["confirmed"]:
            if quote["awaiting_attachment"]:
                task = _upsert_task(
                    db, message, "awaiting_attachment", quote_snapshot=quote,
                    template_id=template.id, template_snapshot=_template_snapshot(template),
                    deadline_at=deadline, error_message="等待 IMAP 同步报价附件",
                )
                db.commit()
                return task
            if quote["errors"]:
                task = _upsert_task(
                    db, message, "manual_review", quote_snapshot=quote,
                    template_id=template.id, template_snapshot=_template_snapshot(template),
                    deadline_at=deadline, error_message="; ".join(quote["errors"])[:1000],
                )
                db.commit()
                return task
            db.commit()
            return None

        # received_at_estimated 表示 received_at 是入库时刻的推算值而非邮件真实时间
        # （见 models/message.py）。两小时新鲜度窗口无从确认时，确认报价也不得自动
        # 外发——宁可多进人工队列。
        if message.received_at_estimated:
            task = _upsert_task(
                db, message, "manual_review", quote_snapshot=quote,
                template_id=template.id, template_snapshot=_template_snapshot(template),
                deadline_at=deadline,
                error_message="收信时间为入库推算值，无法确认两小时新鲜度窗口，需人工确认后发送",
            )
            db.commit()
            return task

        if quote["awaiting_attachment"]:
            task = _upsert_task(
                db, message, "awaiting_attachment", quote_snapshot=quote,
                template_id=template.id, template_snapshot=_template_snapshot(template),
                deadline_at=deadline, error_message="等待 IMAP 同步完整报价附件",
            )
            db.commit()
            return task

        # Any attachment parse failure makes the complete quote context unsafe.
        if quote["errors"]:
            task = _upsert_task(
                db, message, "manual_review", quote_snapshot=quote,
                template_id=template.id, template_snapshot=_template_snapshot(template),
                deadline_at=deadline, error_message="; ".join(quote["errors"])[:1000],
            )
            db.commit()
            return task

        recipient = (message.to_email or "").strip().lower()
        if not recipient or recipient.endswith("@snov.local") or recipient.endswith("@provider.local"):
            task = _upsert_task(
                db, message, "manual_review", quote_snapshot=quote,
                template_id=template.id, template_snapshot=_template_snapshot(template),
                deadline_at=deadline,
                error_message="无法识别原收件邮箱，请在邮箱配置页重新同步历史邮件",
            )
            db.commit()
            return task
        credential = db.query(MailboxCredential).filter(
            func.lower(MailboxCredential.email) == recipient,
            MailboxCredential.enabled.is_(True),
        ).first()
        if not credential:
            task = _upsert_task(
                db, message, "manual_review", quote_snapshot=quote,
                template_id=template.id, template_snapshot=_template_snapshot(template),
                deadline_at=deadline, error_message=f"原收件邮箱 {recipient} 未配置或未启用",
            )
            db.commit()
            return task
        if not credential.smtp_verified_at:
            task = _upsert_task(
                db, message, "manual_review", quote_snapshot=quote,
                template_id=template.id, template_snapshot=_template_snapshot(template),
                mailbox_credential_id=credential.id, deadline_at=deadline,
                error_message=f"原收件邮箱 {recipient} 尚未通过 SMTP 测试",
            )
            db.commit()
            return task
        if not message.rfc_message_id:
            task = _upsert_task(
                db, message, "awaiting_attachment", quote_snapshot=quote,
                template_id=template.id, template_snapshot=_template_snapshot(template),
                mailbox_credential_id=credential.id, deadline_at=deadline,
                error_message="等待 IMAP 同步 RFC Message-ID",
            )
            db.commit()
            return task
        if datetime.utcnow() >= deadline:
            task = _upsert_task(
                db, message, "manual_review", quote_snapshot=quote,
                template_id=template.id, template_snapshot=_template_snapshot(template),
                mailbox_credential_id=credential.id, deadline_at=deadline,
                error_message="报价识别完成时已超过两小时自动发送窗口",
            )
            db.commit()
            return task

        summary = quote_summary(quote)
        question_response = _generate_question_response(message, template)
        values = {
            "creator_name": thread.kol.name if thread.kol else message.from_email,
            "campaign_name": thread.campaign_name or thread.campaign_id or "",
            "original_subject": _safe_original_subject(message.subject or thread.subject or ""),
            "quote_summary": summary,
            "question_response": question_response,
            "sender_name": template.signature,
            "sender_email": credential.email,
            "signature": template.signature,
        }
        subject = _render(template.subject_template, values).strip()
        body = _render(template.body_template, values).strip()
        validation_error = _validate_draft(body, quote)
        delay = random.randint(
            settings.AUTO_REPLY_MIN_DELAY_MINUTES,
            settings.AUTO_REPLY_MAX_DELAY_MINUTES,
        )
        scheduled_at = (message.received_at or datetime.utcnow()) + timedelta(minutes=delay)
        if scheduled_at < datetime.utcnow():
            scheduled_at = datetime.utcnow()
        status = "manual_review" if validation_error else "queued"
        task = _upsert_task(
            db, message, status, quote_snapshot=quote, template_id=template.id,
            template_snapshot=_template_snapshot(template), mailbox_credential_id=credential.id,
            draft_subject=subject, draft_body=body, scheduled_at=scheduled_at,
            deadline_at=deadline, error_message=validation_error, manual_override=False,
        )
        db.commit()
        db.refresh(task)
        return task
    except Exception:
        db.rollback()
        logger.exception("自动回复评估失败 message=%s", message_id)
        return None
    finally:
        db.close()


def _daily_sent_count(db: Session, credential: MailboxCredential) -> int:
    start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return db.query(SendLog).filter(
        SendLog.provider == "smtp",
        SendLog.status == "sent",
        SendLog.sent_at >= start,
        SendLog.provider_lead_id == credential.email,
    ).count()


def send_due_task(task_id: int) -> None:
    """发送一个到期的自动回复草稿。

    遵循 DATABASE_DEVELOPMENT.md §8：SMTP 发送不得在数据库 session 内进行。
    流程：短事务完成前置校验并用条件 UPDATE 把 ``queued`` 原子改成 ``sending``
    （claim，抢不到即放弃，防调度器与 send-now 双发）→ 关闭 session →
    调 SMTP send_message → 新短事务写发送结果。``sending`` 标记配合
    :func:`recover_interrupted_deliveries` 保证崩溃后可恢复（§8 line 469）。
    """
    # ============ 读取 + 校验阶段（持有 session）============
    db = SessionLocal()
    try:
        task = db.get(ScheduledReply, task_id)
        now = datetime.utcnow()
        if not task or task.status != "queued" or not task.scheduled_at or task.scheduled_at > now:
            return
        global_template = ensure_global_template(db)
        if not global_template.auto_send_enabled and not task.manual_override:
            task.status = "cancelled"
            task.error_message = "自动回复总开关已关闭"
            db.commit()
            return
        if not task.manual_override and (not task.deadline_at or now > task.deadline_at):
            task.status = "manual_review"
            task.error_message = "已超过两小时自动发送窗口"
            db.commit()
            return
        source = db.get(Message, task.source_message_id)
        credential = db.get(MailboxCredential, task.mailbox_credential_id)
        thread = db.get(Thread, task.thread_id)
        if not source or not credential or not thread or not source.rfc_message_id:
            task.status = "manual_review"
            task.error_message = "发送所需的来源邮件或邮箱凭据不完整"
            db.commit()
            return
        newer = db.query(Message).filter(
            Message.thread_id == task.thread_id,
            Message.id != source.id,
            or_(
                Message.received_at > source.received_at,
                and_(Message.received_at == source.received_at, Message.id > source.id),
            ),
        ).order_by(Message.received_at.desc(), Message.id.desc()).first()
        if newer:
            task.status = "superseded" if newer.direction == "inbound" else "cancelled"
            task.error_message = "发送前检测到更新的邮件"
            db.commit()
            return
        if _daily_sent_count(db, credential) >= settings.MAX_DAILY_SEND_PER_MAILBOX:
            task.status = "manual_review"
            task.error_message = "原邮箱已达到每日发送上限"
            db.commit()
            return

        is_manual_draft = bool((task.quote_snapshot or {}).get("manual"))
        validation_error = None if is_manual_draft else _validate_draft(
            task.draft_body or "", task.quote_snapshot or {}
        )
        if validation_error:
            task.status = "manual_review"
            task.error_message = validation_error
            db.commit()
            return
        email_message = build_reply_message(
            from_email=credential.email,
            to_email=source.from_email,
            subject=task.draft_subject or f"Re: {_safe_original_subject(source.subject or '')}",
            body=task.draft_body or "",
            in_reply_to=source.rfc_message_id,
            references=source.references or "",
        )

        # 提取 SMTP 后写结果所需的全部值到局部变量（session 即将关闭，ORM 惰性属性会失效）。
        ctx = {
            "thread_id": thread.id,
            "kol_id": thread.kol_id,
            "from_email": credential.email,
            "to_email": source.from_email,
            "draft_body": task.draft_body,
            "rfc_in_reply_to": source.rfc_message_id,
            "references": source.references or "",
        }
        # 同理：commit（expire_on_commit=True）会让 credential 属性全部过期、close 后
        # detached，SMTP 阶段再读会抛 DetachedInstanceError。先快照成纯对象。
        smtp_credential = SmtpCredentialSnapshot.from_credential(credential)

        # 原子抢占（claim）：/tasks/{id}/send-now 的 BackgroundTask 与 30 秒调度器
        # 可能同时通过上面的校验。条件 UPDATE 保证只有一方能把 queued 改成 sending
        # （SQLite/PG 均原子）；rowcount 为 0 说明已被并发方抢走或状态已变，放弃发送。
        claimed = db.query(ScheduledReply).filter(
            ScheduledReply.id == task_id,
            ScheduledReply.status == "queued",
        ).update({"status": "sending", "error_message": None}, synchronize_session=False)
        if not claimed:
            db.rollback()
            return
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("自动回复发送任务前置阶段失败 task=%s", task_id)
        return
    finally:
        db.close()

    # ============ SMTP 阶段（无 DB session）============
    try:
        send_message(smtp_credential, email_message)
    except PreSendError as exc:
        _record_send_failure(task_id, exc, is_ambiguous=False)
        return
    except AmbiguousDeliveryError as exc:
        _record_send_failure(task_id, exc, is_ambiguous=True)
        return
    except Exception as exc:
        # 意外异常发生在 SMTP 交互期间，无法判断 DATA 是否已被服务器接收；
        # 按保守哲学视为投递结果不确定：进 manual_review、不自动重试，
        # 且不让异常逃出去打断 process_due_replies 的整批任务。
        logger.exception("自动回复 SMTP 阶段意外异常 task=%s", task_id)
        _record_send_failure(task_id, exc, is_ambiguous=True)
        return

    # ============ 写结果阶段（新短事务）============
    db = SessionLocal()
    try:
        task = db.get(ScheduledReply, task_id)
        if not task:
            logger.warning("自动回复任务在 SMTP 发送后消失: %s", task_id)
            return
        # 防御：SMTP 期间任务可能被 recover_interrupted_deliveries 改为 manual_review
        #（发送超时）。若已被人工/恢复流程接管，不覆盖其状态。
        if task.status != "sending":
            logger.warning(
                "自动回复任务 %s 在 SMTP 后状态已变为 %s，跳过写结果",
                task_id, task.status,
            )
            return
        rfc_id = str(email_message["Message-ID"]).strip().strip("<>")
        outbound = Message(
            thread_id=ctx["thread_id"], direction="outbound", from_email=ctx["from_email"],
            to_email=ctx["to_email"], subject=str(email_message["Subject"]),
            body_text=ctx["draft_body"],
            body_html=email_message.get_body(preferencelist=("html",)).get_content(),
            message_id=rfc_id, rfc_message_id=rfc_id,
            in_reply_to=ctx["rfc_in_reply_to"], references=str(email_message["References"]),
            is_read=True, received_at=datetime.utcnow(),
        )
        db.add(outbound)
        db.flush()
        db.add(SendLog(
            thread_id=ctx["thread_id"], kol_id=ctx["kol_id"], provider="smtp",
            provider_lead_id=ctx["from_email"], status="sent", sent_at=datetime.utcnow(),
        ))
        task.status = "sent"
        task.outbound_message_id = outbound.id
        task.error_message = None
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("自动回复发送结果写入失败 task=%s", task_id)
    finally:
        db.close()


def _record_send_failure(task_id: int, exc: BaseException, *, is_ambiguous: bool) -> None:
    """SMTP 发送失败后，在新短事务里记录重试/失败/人工复核状态。

    抽出自 ``send_due_task`` 的异常分支，使 SMTP 阶段保持无 session。
    有界重试逻辑（retry_count<3 且在窗口内 → 重新排队，否则 failed）原样保留。
    """
    db = SessionLocal()
    try:
        task = db.get(ScheduledReply, task_id)
        if not task:
            return
        if is_ambiguous:
            task.status = "manual_review"
            task.error_message = f"投递结果不确定，未自动重试: {exc}"[:1000]
            db.commit()
            return
        # PreSendError 分支：有界重试
        task.retry_count = (task.retry_count or 0) + 1
        retry_in_window = task.manual_override or (
            task.deadline_at
            and datetime.utcnow() + timedelta(minutes=5) < task.deadline_at
        )
        if task.retry_count < 3 and retry_in_window:
            task.status = "queued"
            task.scheduled_at = datetime.utcnow() + timedelta(minutes=5)
        else:
            task.status = "failed"
        task.error_message = str(exc)[:1000]
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("自动回复发送失败状态记录失败 task=%s", task_id)
    finally:
        db.close()


def process_due_replies() -> None:
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        expired = db.query(ScheduledReply).filter(
            ScheduledReply.status == "awaiting_attachment",
            ScheduledReply.deadline_at <= now,
        ).all()
        for task in expired:
            task.status = "manual_review"
            task.error_message = "等待附件或 RFC 邮件头超过两小时"
        due_ids = [row[0] for row in db.query(ScheduledReply.id).filter(
            ScheduledReply.status == "queued",
            ScheduledReply.scheduled_at <= now,
        ).limit(20).all()]
        db.commit()
    finally:
        db.close()
    for task_id in due_ids:
        try:
            send_due_task(task_id)
        except Exception:
            # 双保险：单个任务的意外异常不允许打断整批，其余到期任务照常处理。
            logger.exception("自动回复任务处理异常，继续批次内下一个 task=%s", task_id)


def recover_interrupted_deliveries() -> None:
    db = SessionLocal()
    try:
        tasks = db.query(ScheduledReply).filter(ScheduledReply.status == "sending").all()
        for task in tasks:
            task.status = "manual_review"
            task.error_message = "服务在投递过程中重启，结果不确定，未自动重试"
        db.commit()
    finally:
        db.close()
