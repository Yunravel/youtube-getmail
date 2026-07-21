"""IMAP 附件同步业务编排：连邮箱 → 抓邮件 → 匹配中台 message → 存附件 → 回填元数据。

两种模式：
  - mode="unread"：定时轮询用，只抓未读，成功后标记已读。
  - mode="manual"：前端手动触发，按 since_days 抓指定天数内所有邮件，不动已读状态。

邮件→message 匹配：from_email + subject(去 Re:/Fwd: 前缀) + 时间窗口 ±3 天。
匹配失败的邮件记日志（KOL 不在中台 / 跨 campaign），不阻塞。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Callable, Optional

from sqlalchemy import and_
from sqlalchemy.orm import Session

from config import settings
from db import SessionLocal
from models import MailboxCredential, Message
from services.attachments import merge_attachments
from services.crypto import decrypt_password
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
        processed_uids: list[str] = []
        for index, fetched in enumerate(msgs, start=1):
            if on_progress:
                on_progress(index, len(msgs))
            try:
                matched = _find_matching_message(
                    db, fetched.from_email, fetched.subject, fetched.date
                )
                if not matched:
                    stats["no_match"] += 1
                    logger.info(
                        "邮件无匹配 thread: from=%s subj=%r [%s]",
                        fetched.from_email, fetched.subject[:50], email_addr
                    )
                    continue

                stats["matched"] += 1
                if not fetched.attachments:
                    stats["skipped_no_attach"] += 1
                    continue

                # 存盘 + 回填 message.attachments
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

    return aggregate
