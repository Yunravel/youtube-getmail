"""IMAP 附件同步业务编排：连邮箱 → 抓邮件 → 匹配中台 message → 存附件 → 回填元数据。

两种模式：
  - mode="unread"：定时轮询用，只抓未读，成功后标记已读。
  - mode="manual"：前端手动触发，按 since_days 抓指定天数内所有邮件，不动已读状态。

邮件→message 匹配：from_email + subject(去 Re:/Fwd: 前缀) + 时间窗口 ±3 天。
匹配失败的邮件：若确认是外联回信（有 In-Reply-To/References 且主题对得上已知
Campaign 会话），直接落库为中台 inbound 消息——Snov 只回传 prospect 登记邮箱
发来的回信，经纪人代回 / Zendesk / Intercom 等第三方地址的真实回信只有这里能
补回来。其余（DSN/DMARC/安全提醒等噪声）记日志跳过，不阻塞。
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta
from typing import Callable, Optional

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from config import settings
from db import SessionLocal
from models import Kol, MailboxCredential, Message, Thread
from services.attachments import merge_attachments
from services.crypto import decrypt_password
from services.email_content import clean_email_body, is_html_email_body
from services.email_utils import ensure_kol_email
from services.imap_client import FetchedEmail, ImapMailbox

logger = logging.getLogger(__name__)

# 匹配时间窗口（±天）。KOL 回信经 Snov 中转可能比 Gmail 收信晚几小时到几天。
MATCH_WINDOW_DAYS = 3

# subject 前缀清理：Re:/Fwd:/自动回复 等，让两边 subject 能对上
_SUBJECT_PREFIX_RE = re.compile(r"^\s*((re|fwd|fw|aw|wg)\s*:\s*)+", re.IGNORECASE)


def _normalize_subject(subject: str) -> str:
    """去掉 Re:/Fwd: 等前缀和首尾空白，用于跨来源 subject 匹配。"""
    if not subject:
        return ""
    cleaned = subject
    while True:
        new = _SUBJECT_PREFIX_RE.sub("", cleaned).strip()
        if new == cleaned:
            return new
        cleaned = new


def _safe_filename(filename: str, fallback: str = "attachment") -> str:
    """清理文件名：去路径分隔符、控制字符、折叠空白，防目录穿越。"""
    if not filename:
        return fallback
    # 去掉所有路径分隔符（防 ../../etc/passwd）
    name = re.sub(r"[\\/]", "_", filename)
    # 去掉控制字符和换行（MIME 折叠空白会带 \n）
    name = re.sub(r"[\x00-\x1f\x7f\s]+", " ", name).strip()
    # 限制长度
    if len(name) > 180:
        stem, dot, ext = name.rpartition(".")
        if dot and len(ext) <= 10:
            name = stem[:180 - len(ext) - 1] + "." + ext
        else:
            name = name[:180]
    return name or fallback


def _attachment_metadata(filename: str, content_type: str, size: int, local_path: str) -> dict:
    """构造写入 message.attachments 的元数据 dict（与 attachments.py 同构 + local_path）。"""
    return {
        "id": None,
        "name": filename,
        "url": None,
        "size": size,
        "content_type": content_type or None,
        "local_path": local_path,  # 相对 ATTACHMENT_STORAGE_DIR 的路径
        "source": "imap",          # 区分网盘链接（link）和真附件（imap）
    }


def _find_matching_message(
    db: Session,
    from_email: str,
    subject: str,
    received_at: Optional[datetime],
) -> Optional[Message]:
    """在中台找与这封 IMAP 邮件对应的 inbound message。

    匹配规则：from_email 一致 + subject 去前缀后包含 + 时间窗口 ±3 天。
    返回最近的匹配；找不到返回 None。
    """
    if not from_email:
        return None
    normalized = from_email.lower().strip()
    target_subject = _normalize_subject(subject)

    query = db.query(Message).filter(
        Message.direction == "inbound",
        Message.from_email == normalized,
    )
    if received_at:
        start = received_at - timedelta(days=MATCH_WINDOW_DAYS)
        end = received_at + timedelta(days=MATCH_WINDOW_DAYS)
        query = query.filter(Message.received_at.between(start, end))

    candidates = query.order_by(Message.received_at.desc()).all()
    if not candidates:
        return None
    if not target_subject:
        return candidates[0]

    # subject 精确匹配优先；否则取时间最近的
    for m in candidates:
        if _normalize_subject(m.subject or "") == target_subject:
            return m
    # 宽松：一方包含另一方
    for m in candidates:
        ms = _normalize_subject(m.subject or "")
        if ms and (ms in target_subject or target_subject in ms):
            return m
    return candidates[0]  # 同发件人时间最近的，最可能是对的


# ===== 未匹配真实回信落库（修复"Snov 收不到第三方地址回信"）=====

# 明显的系统邮件发件人（本地部分）：退信、DMARC 报告、平台通知等，不是 KOL 回信。
_NOISE_LOCALPART_RE = re.compile(
    r"^(mailer-daemon|postmaster|bounce[s]?|abuse"
    r"|no-?reply[-.\w]*|do-?not-?reply|donotreply[-.\w]*"
    r"|notify|notification[s]?|alert[s]?"
    r"|dmarcreport|noreply-dmarc[-\w]*|dmarc[-.\w]*)$",
    re.IGNORECASE,
)
# 明显的系统邮件主题特征（双保险：个别 DSN 会带 In-Reply-To 头）。
_NOISE_SUBJECT_RE = re.compile(
    r"(delivery status notification|undeliverable|mail delivery (?:failed|subsystem)"
    r"|report domain:|dmarc|security alert|verification code"
    r"|安全提醒|安全提示|重要安全提醒|验证码)",
    re.IGNORECASE,
)

# 主题匹配的最短长度：太短的主题（如 "hi"）撞库概率高，不作为外联证据。
_MIN_SUBJECT_MATCH_LEN = 8


def _is_noise_email(fetched: FetchedEmail) -> bool:
    """判定是否为系统/通知类邮件（退信、DMARC、安全提醒等），此类不落库。"""
    localpart = (fetched.from_email or "").split("@", 1)[0]
    if _NOISE_LOCALPART_RE.match(localpart):
        return True
    if _NOISE_SUBJECT_RE.search(fetched.subject or ""):
        return True
    return False


def _load_known_outreach_subjects(db: Session) -> set[str]:
    """取库里所有会话主题（去 Re:/Fwd: 前缀）作为"这是我们外联"的比对基准。

    会话按 KOL 聚合，量级与 KOL 数相同，全量加载可接受；每轮邮箱同步只加载一次。
    """
    subjects: set[str] = set()
    for (subject,) in db.query(Thread.subject).all():
        normalized = _normalize_subject(subject or "")
        if len(normalized) >= _MIN_SUBJECT_MATCH_LEN:
            subjects.add(normalized)
    return subjects


def _matches_known_outreach(
    db: Session,
    fetched: FetchedEmail,
    known_subjects: set[str],
) -> bool:
    """这封未匹配邮件是否确定是对我们外联的回复。

    证据一：In-Reply-To 指向库里任一 message（webhook 接通后可命中已发邮件）。
    证据二：主题去前缀后与库里任一会话主题一致或互相包含（同一 Campaign 的
    回信主题一致，第三方代回也沿用原主题）。
    """
    if fetched.in_reply_to:
        hit = db.query(Message.id).filter(
            or_(
                Message.rfc_message_id == fetched.in_reply_to,
                Message.message_id == fetched.in_reply_to,
            )
        ).first()
        if hit:
            return True

    target = _normalize_subject(fetched.subject or "")
    if len(target) < _MIN_SUBJECT_MATCH_LEN:
        return False
    for known in known_subjects:
        if known == target or known in target or target in known:
            return True
    return False


_MSGID_RE = re.compile(r"<([^<>\s]+)>")


def _resolve_reference_thread(db: Session, fetched: FetchedEmail) -> Optional[Thread]:
    """用 In-Reply-To + References 全链定位回信所属的既有会话。

    经纪人代回 / 达人换邮箱回信时 from_email 匹配不到 KOL，但引用链里任一
    Message-ID 命中库内消息即可确定原会话，避免为同一达人重复建裸档
    （2026-07-26 补录产生 5 个此类裸档，见 kol 2412-2417 归并记录）。
    引用链从最近一封往前找；兼容带/不带尖括号两种存储格式。
    """
    candidates: list[str] = []
    for raw in (fetched.in_reply_to or "", fetched.references or ""):
        found = _MSGID_RE.findall(raw)
        candidates.extend(found if found else ([raw.strip()] if raw.strip() else []))
    seen: set[str] = set()
    ordered = [c for c in candidates if not (c in seen or seen.add(c))]
    for ref in reversed(ordered):
        variants = (ref, f"<{ref}>")
        hit = (
            db.query(Message)
            .filter(
                or_(
                    Message.rfc_message_id.in_(variants),
                    Message.message_id.in_(variants),
                )
            )
            .order_by(Message.id.desc())
            .first()
        )
        if hit is not None and hit.thread is not None and hit.thread.kol_id:
            return hit.thread
    return None


def _ingest_unmatched_email(
    db: Session,
    fetched: FetchedEmail,
    mailbox_email: str,
    known_subjects: set[str],
) -> tuple[Optional[Message], Optional[int]]:
    """把 Snov 未回传、但确认是外联回信的 IMAP 邮件落库为 inbound 消息。

    返回 ``(message, created_kol_id)``：判定不通过或已存在时 message 为 None；
    created_kol_id 仅在本次新建了裸 KOL 时非空，调用方据此触发画像补全。
    归属优先级：引用链命中的原会话（发件人记为该 KOL 的别名邮箱）→
    from_email 命中的既有 KOL → 新建裸 KOL。幂等：优先用 RFC Message-ID，
    缺失时用内容指纹。
    """
    if not settings.IMAP_INGEST_UNMATCHED_ENABLED:
        return None, None
    from_email = (fetched.from_email or "").strip().lower()
    if not from_email or "@" not in from_email:
        return None, None
    if from_email == (mailbox_email or "").strip().lower():
        return None, None  # 自己发的
    if _is_noise_email(fetched):
        return None, None
    if not (fetched.in_reply_to or fetched.references):
        return None, None  # 不是对任何邮件的回复（新闻邮件/通知），不落库
    if not _matches_known_outreach(db, fetched, known_subjects):
        return None, None

    # 幂等 ID：imap:<RFC Message-ID>；无 Message-ID 时退化为内容指纹。
    if fetched.message_id:
        message_id = f"imap:{fetched.message_id}"
    else:
        identity = "|".join([
            from_email,
            fetched.subject or "",
            (fetched.date.isoformat() if fetched.date else ""),
        ])
        message_id = f"imap:{hashlib.sha256(identity.encode()).hexdigest()}"

    dedup_conditions = [Message.message_id == message_id]
    if fetched.message_id:
        dedup_conditions.append(Message.rfc_message_id == fetched.message_id)
    if db.query(Message.id).filter(or_(*dedup_conditions)).first():
        return None, None

    created_kol_id: Optional[int] = None
    # 查档覆盖 kol_email 别名表：换邮箱回信/代回地址若已是别名，直接归位原 KOL。
    from services.email_utils import find_kol_by_any_email

    kol = find_kol_by_any_email(db, from_email)
    resolved_thread = _resolve_reference_thread(db, fetched)
    if resolved_thread is not None and (kol is None or kol.id == resolved_thread.kol_id):
        # 引用链命中原会话：回信挂回原 KOL，新地址登记为其别名邮箱，
        # 不再为同一达人新建裸档（画像随原 KOL，飞书行保持完整）。
        kol = resolved_thread.kol
        ensure_kol_email(
            db, kol.id, from_email, is_primary=False, source="imap_reference_match"
        )
        logger.info(
            "引用链命中原会话: from=%s → kol=%s(%s) thread=%d",
            from_email, kol.id, kol.name, resolved_thread.id,
        )
    elif kol is None:
        kol = Kol(
            name=fetched.from_name or from_email.split("@", 1)[0],
            email=from_email,
            status="in_conversation",
        )
        db.add(kol)
        db.flush()
        ensure_kol_email(db, kol.id, from_email, is_primary=True, source="imap_unmatched")
        created_kol_id = kol.id

    subject = (fetched.subject or "").strip() or "(no subject)"
    thread = resolved_thread if (
        resolved_thread is not None and resolved_thread.kol_id == kol.id
    ) else (
        db.query(Thread)
        .filter(Thread.kol_id == kol.id, Thread.status != "closed")
        .order_by(Thread.updated_at.desc())
        .first()
    )
    if not thread:
        thread = Thread(kol_id=kol.id, subject=subject, status="open")
        db.add(thread)
        db.flush()

    raw_body = fetched.body_text or fetched.body_html or ""
    body = clean_email_body(raw_body) or "[IMAP 回信；正文为空]"
    message = Message(
        thread_id=thread.id,
        direction="inbound",
        from_email=from_email,
        to_email=(mailbox_email or "").strip().lower() or "unknown@imap.local",
        subject=subject,
        body_text=body,
        body_html=fetched.body_html if is_html_email_body(fetched.body_html) else None,
        message_id=message_id,
        rfc_message_id=fetched.message_id or None,
        in_reply_to=fetched.in_reply_to or None,
        references=fetched.references or None,
        received_at=fetched.date or datetime.utcnow(),
        # Date 头缺失/解析失败时 received_at 是入库时刻，不是真实收信时间；
        # 标记 estimated，自动回复的新鲜度闸门据此拒绝当"新邮件"放行。
        received_at_estimated=fetched.date is None,
    )
    db.add(message)
    db.flush()
    from services.auto_reply import supersede_thread_tasks
    supersede_thread_tasks(db, thread.id, message.id)
    thread.reply_count = (thread.reply_count or 0) + 1
    kol.status = "in_conversation"
    logger.info(
        "IMAP 补录 Snov 未回传的回信: from=%s subj=%r → message=%d thread=%d [%s]",
        from_email, subject[:50], message.id, thread.id, mailbox_email,
    )
    return message, created_kol_id


def _apply_mailbox_context(
    db: Session,
    matched: Message,
    fetched: FetchedEmail,
    mailbox_email: str,
) -> int:
    """Persist the mailbox that actually received the IMAP message.

    Provider webhooks often store an unknown recipient. The authenticated IMAP
    mailbox is authoritative for reply routing. Exact duplicate provider rows
    are repaired together so a later duplicate cannot restore the placeholder.
    """
    recipient = (mailbox_email or "").strip().lower()
    if not recipient:
        return 0
    candidates = [matched]
    if matched.received_at:
        peers = db.query(Message).filter(
            Message.thread_id == matched.thread_id,
            Message.direction == "inbound",
            Message.from_email == matched.from_email,
            Message.received_at == matched.received_at,
            Message.id != matched.id,
        ).all()
        target_subject = _normalize_subject(matched.subject or "")
        candidates.extend(
            peer for peer in peers
            if _normalize_subject(peer.subject or "") == target_subject
        )

    repaired = 0
    for item in candidates:
        if (item.to_email or "").strip().lower() != recipient:
            item.to_email = recipient
            repaired += 1
        if fetched.message_id:
            item.rfc_message_id = fetched.message_id
        if fetched.in_reply_to:
            item.in_reply_to = fetched.in_reply_to
        if fetched.references:
            item.references = fetched.references
    return repaired


def _save_attachment_files(
    fetched: FetchedEmail,
    message_id: int,
) -> list[tuple[dict, int]]:
    """把这封邮件的所有附件写到磁盘，返回 [(metadata, payload_size), ...]。

    存储路径：<ATTACHMENT_STORAGE_DIR>/<message_id>/<safe_filename>
    同名附件加序号避免覆盖。
    """
    if not fetched.attachments:
        return []
    msg_dir = settings.attachment_storage_path / str(message_id)
    msg_dir.mkdir(parents=True, exist_ok=True)

    used_names: set[str] = set()
    results: list[tuple[dict, int]] = []
    for att in fetched.attachments:
        safe_name = _safe_filename(att.filename)
        # 同目录去重
        candidate = safe_name
        n = 1
        while candidate in used_names:
            stem, dot, ext = safe_name.rpartition(".")
            candidate = f"{stem}_{n}.{ext}" if dot else f"{safe_name}_{n}"
            n += 1
        used_names.add(candidate)

        file_path = msg_dir / candidate
        file_path.write_bytes(att.data)

        # local_path 存相对路径（相对于 storage dir），下载端点拼绝对路径
        local_path = f"{message_id}/{candidate}"
        meta = _attachment_metadata(candidate, att.content_type, att.size, local_path)
        results.append((meta, att.size))
    return results


def sync_one_mailbox(
    credential: MailboxCredential,
    mode: str = "unread",
    since_days: Optional[int] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> dict:
    """同步单个邮箱。返回统计 dict。

    mode: "unread"（轮询，只未读+标已读）或 "manual"（手动，按 since_days，不动已读）。
    """
    email_addr = credential.email
    password = decrypt_password(credential.encrypted_password)
    if not password:
        logger.warning("邮箱 %s 无密码，跳过", email_addr)
        return {"email": email_addr, "skipped_no_password": True}

    only_unread = mode == "unread"
    stats = {
        "email": email_addr,
        "fetched": 0,
        "matched": 0,
        "attached": 0,
        "skipped_no_attach": 0,
        "no_match": 0,
        "ingested": 0,
        "repaired_recipients": 0,
        "errors": 0,
    }

    try:
        with ImapMailbox(email_addr, password, credential.imap_host, credential.imap_port) as mb:
            msgs = mb.fetch_unread(since_days=since_days, only_unread=only_unread, limit=500)
    except Exception as e:
        logger.error("同步邮箱 %s 失败: %s", email_addr, e)
        stats["errors"] = 1
        stats["error_message"] = str(e)
        return stats

    stats["fetched"] = len(msgs)
    db = SessionLocal()
    try:
        known_subjects = _load_known_outreach_subjects(db)
        processed_uids: list[str] = []
        for index, fetched in enumerate(msgs, start=1):
            if on_progress:
                on_progress(index, len(msgs))
            try:
                matched = _find_matching_message(
                    db, fetched.from_email, fetched.subject, fetched.date
                )
                if not matched:
                    # Snov 未回传的第三方地址真实回信：直接落库补回，
                    # 判定不通过（噪声/无外联证据）才计入 no_match。
                    ingested, created_kol_id = _ingest_unmatched_email(
                        db, fetched, email_addr, known_subjects
                    )
                    if ingested is None:
                        stats["no_match"] += 1
                        logger.info(
                            "邮件无匹配 thread: from=%s subj=%r [%s]",
                            fetched.from_email, fetched.subject[:50], email_addr
                        )
                        continue
                    saved = _save_attachment_files(fetched, ingested.id)
                    if saved:
                        ingested.attachments = merge_attachments(
                            ingested.attachments or [], [m for m, _ in saved]
                        )
                        stats["attached"] += len(saved)
                    db.commit()
                    stats["ingested"] += 1
                    processed_uids.append(fetched.uid)
                    # 与 webhook 路径一致的后处理：AI 意向分析（内部会触发
                    # 自动回复评估）→ 报价来源分析 → 画像补全（仅新建裸 KOL，
                    # 补全成功会自行刷新飞书行）→ 飞书持久同步。
                    from api.webhook import analyze_inbound_message
                    analyze_inbound_message(ingested.id)
                    from services.quote_source_analysis import (
                        analyze_and_save_quote_sources,
                    )
                    analyze_and_save_quote_sources(ingested.id)
                    if created_kol_id:
                        from services.kol_enrich import enrich_reply_kol
                        enrich_reply_kol(created_kol_id)
                    from services.feishu_push import enqueue_message_sync
                    enqueue_message_sync(ingested.id)
                    continue

                stats["matched"] += 1
                stats["repaired_recipients"] += _apply_mailbox_context(
                    db, matched, fetched, email_addr
                )
                if not fetched.attachments:
                    stats["skipped_no_attach"] += 1
                    db.commit()
                    processed_uids.append(fetched.uid)
                    from services.auto_reply import evaluate_message_for_auto_reply
                    evaluate_message_for_auto_reply(matched.id)
                    continue

                # Persist only genuinely new IMAP files. Recent-mode polling
                # revisits messages so that already-read mail is not missed.
                existing_keys = {
                    (str(item.get("name") or ""), int(item.get("size") or 0))
                    for item in (matched.attachments or [])
                    if item.get("source") == "imap"
                }
                original_attachments = fetched.attachments
                fetched.attachments = [
                    item for item in original_attachments
                    if (item.filename, int(item.size or 0)) not in existing_keys
                ]
                saved = _save_attachment_files(fetched, matched.id)
                if saved:
                    new_metas = [m for m, _ in saved]
                    merged = merge_attachments(matched.attachments or [], new_metas)
                    matched.attachments = merged
                    db.commit()
                    stats["attached"] += len(saved)
                    logger.info(
                        "已抓取 %d 个附件 → message=%d thread=%d [%s]",
                        len(saved), matched.id, matched.thread_id, email_addr
                    )
                else:
                    # RFC headers may still have changed even when all files
                    # were already persisted by an earlier polling cycle.
                    db.commit()
                from services.auto_reply import evaluate_message_for_auto_reply
                evaluate_message_for_auto_reply(matched.id)
                if saved:
                    from services.quote_source_analysis import (
                        analyze_and_save_quote_sources,
                    )
                    outcome = analyze_and_save_quote_sources(matched.id)
                    if outcome.get("status") == "analyzed":
                        from services.feishu_push import enqueue_message_sync
                        enqueue_message_sync(matched.id)
                processed_uids.append(fetched.uid)
            except Exception as e:
                db.rollback()
                stats["errors"] += 1
                logger.exception("处理邮件失败 uid=%s [%s]: %s", fetched.uid, email_addr, e)

        # 轮询模式下，成功处理（有附件或确认无附件）的邮件标记已读，避免重复抓
        if only_unread and processed_uids:
            try:
                with ImapMailbox(email_addr, password, credential.imap_host, credential.imap_port) as mb:
                    for uid in processed_uids:
                        mb.mark_read(uid)
            except Exception as e:
                logger.warning("标记已读失败 [%s]: %s", email_addr, e)

        # 更新凭据状态
        cred = db.query(MailboxCredential).get(credential.id)
        if cred:
            cred.last_synced_at = datetime.utcnow()
            if stats["errors"]:
                cred.last_sync_status = "partial" if stats["attached"] else "failed"
            else:
                cred.last_sync_status = "success"
            cred.last_error = stats.get("error_message")
            db.commit()
    finally:
        db.close()

    return stats


def sync_all_mailboxes(
    mode: str = "unread",
    since_days: Optional[int] = None,
    on_mailbox_progress: Optional[Callable[[int, int, str], None]] = None,
) -> dict:
    """同步所有 enabled 的邮箱。返回聚合统计。

    on_mailbox_progress(done, total, email) 用于前端进度展示。
    """
    db = SessionLocal()
    try:
        creds = db.query(MailboxCredential).filter(MailboxCredential.enabled.is_(True)).all()
        # 脱离 session，每个邮箱单独开 session
        cred_ids = [c.id for c in creds]
    finally:
        db.close()

    total = len(cred_ids)
    aggregate = {
        "mailboxes_total": total,
        "mailboxes_ok": 0,
        "mailboxes_failed": 0,
        "fetched": 0,
        "matched": 0,
        "attached": 0,
        "ingested": 0,
        "repaired_recipients": 0,
        "details": [],
    }

    for index, cid in enumerate(cred_ids, start=1):
        db = SessionLocal()
        try:
            cred = db.query(MailboxCredential).get(cid)
            if not cred:
                continue
            email_addr = cred.email
            if on_mailbox_progress:
                on_mailbox_progress(index, total, email_addr)
            stats = sync_one_mailbox(cred, mode=mode, since_days=since_days)
        finally:
            db.close()

        aggregate["details"].append(stats)
        if stats.get("errors") and not stats.get("attached"):
            aggregate["mailboxes_failed"] += 1
        else:
            aggregate["mailboxes_ok"] += 1
        aggregate["fetched"] += stats.get("fetched", 0)
        aggregate["matched"] += stats.get("matched", 0)
        aggregate["attached"] += stats.get("attached", 0)
        aggregate["ingested"] += stats.get("ingested", 0)
        aggregate["repaired_recipients"] += stats.get("repaired_recipients", 0)

    return aggregate
