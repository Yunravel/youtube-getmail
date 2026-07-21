"""Mailbox conversation listing, filters, and read/star state management."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import String, and_, case, cast, exists, func, or_, select
from sqlalchemy.orm import Bundle, Session, aliased

from db import get_db
from models import Kol, Message, SendLog, Thread

router = APIRouter()

FOLDERS = {"inbox", "sent", "starred", "bounced"}


class MailboxStateIn(BaseModel):
    is_read: Optional[bool] = None
    is_starred: Optional[bool] = None


class MailboxBulkStateIn(MailboxStateIn):
    thread_ids: list[int]


def _validate_state(is_read: Optional[bool], is_starred: Optional[bool]) -> None:
    if is_read is None and is_starred is None:
        raise HTTPException(422, "is_read or is_starred is required")


def _message_stats_subquery():
    attachment_text = func.lower(cast(Message.attachments, String))
    has_attachment = and_(
        Message.attachments.isnot(None),
        attachment_text.notin_(("[]", "null", "{}", "")),
    )
    return (
        select(
            Message.thread_id.label("thread_id"),
            func.count(Message.id).label("message_count"),
            func.sum(case((and_(
                Message.direction == "inbound",
                Message.is_read.is_(False),
            ), 1), else_=0)).label("unread_count"),
            func.max(case((Message.direction == "inbound", 1), else_=0)).label("has_inbound"),
            func.max(case((and_(
                Message.direction == "outbound",
                Message.message_id.isnot(None),
            ), 1), else_=0)).label("has_real_outbound"),
            func.max(case((has_attachment, 1), else_=0)).label("has_attachments"),
        )
        .group_by(Message.thread_id)
        .subquery()
    )


def _latest_messages_subquery():
    return (
        select(
            Message.id.label("id"),
            Message.thread_id.label("thread_id"),
            Message.direction.label("direction"),
            Message.from_email.label("from_email"),
            Message.to_email.label("to_email"),
            Message.subject.label("subject"),
            Message.body_text.label("body_text"),
            Message.received_at.label("received_at"),
            func.row_number().over(
                partition_by=Message.thread_id,
                order_by=(Message.received_at.desc(), Message.id.desc()),
            ).label("row_number"),
        )
        .subquery()
    )


def _folder_condition(folder: str, stats, bounced_threads):
    if folder == "inbox":
        return stats.c.has_inbound == 1
    if folder == "sent":
        return stats.c.has_real_outbound == 1
    if folder == "starred":
        return Thread.is_starred.is_(True)
    return bounced_threads.c.thread_id.isnot(None)


def _base_query(db: Session, q: Optional[str], campaign_id: Optional[str], account: Optional[str]):
    stats = _message_stats_subquery()
    latest = _latest_messages_subquery()
    bounced_threads = (
        select(SendLog.thread_id.label("thread_id"))
        .where(and_(SendLog.status == "bounced", SendLog.thread_id.isnot(None)))
        .group_by(SendLog.thread_id)
        .subquery()
    )

    stats_bundle = Bundle("message_stats", *stats.c)
    latest_bundle = Bundle("latest_message", *latest.c)
    query = (
        db.query(
            Thread,
            Kol,
            stats_bundle,
            latest_bundle,
            bounced_threads.c.thread_id.label("bounced_thread_id"),
        )
        .join(Kol, Kol.id == Thread.kol_id)
        .join(stats, stats.c.thread_id == Thread.id)
        .join(latest, and_(latest.c.thread_id == Thread.id, latest.c.row_number == 1))
        .outerjoin(bounced_threads, bounced_threads.c.thread_id == Thread.id)
    )

    if campaign_id:
        query = query.filter(Thread.campaign_id == campaign_id)

    if account:
        normalized_account = account.strip().lower()
        account_message = aliased(Message)
        account_exists = exists(select(account_message.id).where(and_(
            account_message.thread_id == Thread.id,
            or_(
                and_(
                    account_message.direction == "inbound",
                    func.lower(account_message.to_email) == normalized_account,
                ),
                and_(
                    account_message.direction == "outbound",
                    account_message.message_id.isnot(None),
                    func.lower(account_message.from_email) == normalized_account,
                ),
            ),
        )))
        query = query.filter(account_exists)

    if q and q.strip():
        pattern = f"%{q.strip()}%"
        searched_message = aliased(Message)
        message_match = exists(select(searched_message.id).where(and_(
            searched_message.thread_id == Thread.id,
            or_(
                searched_message.subject.ilike(pattern),
                searched_message.body_text.ilike(pattern),
                searched_message.from_email.ilike(pattern),
                searched_message.to_email.ilike(pattern),
            ),
        )))
        query = query.filter(or_(
            Kol.name.ilike(pattern),
            Kol.email.ilike(pattern),
            Thread.subject.ilike(pattern),
            message_match,
        ))

    return query, stats, latest, bounced_threads


@router.get("")
def list_mailbox(
    folder: str = Query("inbox"),
    q: Optional[str] = None,
    campaign_id: Optional[str] = None,
    account: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    if folder not in FOLDERS:
        raise HTTPException(422, f"folder must be one of: {', '.join(sorted(FOLDERS))}")

    base, stats, latest, bounced_threads = _base_query(db, q, campaign_id, account)
    folder_counts = {
        name: base.filter(_folder_condition(name, stats, bounced_threads)).count()
        for name in ("inbox", "sent", "starred", "bounced")
    }

    folder_query = base.filter(_folder_condition(folder, stats, bounced_threads))
    rows = (
        folder_query
        .order_by(latest.c.received_at.desc(), Thread.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for thread, kol, message_stats, latest_message, bounced_thread_id in rows:
        direction = latest_message.direction
        account_email = latest_message.to_email if direction == "inbound" else latest_message.from_email
        items.append({
            "thread_id": thread.id,
            "kol_id": thread.kol_id,
            "contact_name": kol.name,
            "contact_email": kol.email,
            "subject": latest_message.subject or thread.subject,
            "preview": (latest_message.body_text or "").replace("\n", " ").strip()[:240],
            "message_count": int(message_stats.message_count or 0),
            "unread_count": int(message_stats.unread_count or 0),
            "is_read": int(message_stats.unread_count or 0) == 0,
            "is_starred": bool(thread.is_starred),
            "campaign_id": thread.campaign_id,
            "campaign_name": thread.campaign_name,
            "account_email": account_email,
            "has_attachments": bool(message_stats.has_attachments),
            "last_direction": direction,
            "last_message_at": latest_message.received_at,
            "is_bounced": bounced_thread_id is not None,
        })

    return {
        "items": items,
        "total": folder_counts[folder],
        "page": page,
        "page_size": page_size,
        "folder_counts": folder_counts,
    }


@router.get("/filters")
def mailbox_filters(db: Session = Depends(get_db)):
    campaign_rows = (
        db.query(Thread.campaign_id, Thread.campaign_name)
        .filter(Thread.campaign_id.isnot(None))
        .distinct()
        .order_by(Thread.campaign_name, Thread.campaign_id)
        .all()
    )

    inbound_accounts = db.query(Message.to_email).filter(
        Message.direction == "inbound",
        Message.to_email.contains("@"),
    )
    outbound_accounts = db.query(Message.from_email).filter(
        Message.direction == "outbound",
        Message.message_id.isnot(None),
        Message.from_email.contains("@"),
    )
    account_rows = inbound_accounts.union(outbound_accounts).all()

    return {
        "campaigns": [
            {"id": campaign_id, "name": campaign_name or campaign_id}
            for campaign_id, campaign_name in campaign_rows
        ],
        "accounts": sorted({row[0].strip().lower() for row in account_rows if row[0]}),
    }


def _apply_state(db: Session, threads: list[Thread], state: MailboxStateIn) -> None:
    _validate_state(state.is_read, state.is_starred)
    thread_ids = [thread.id for thread in threads]
    if state.is_starred is not None:
        for thread in threads:
            thread.is_starred = state.is_starred
    if state.is_read is not None:
        db.query(Message).filter(
            Message.thread_id.in_(thread_ids),
            Message.direction == "inbound",
        ).update({Message.is_read: state.is_read}, synchronize_session=False)
    db.commit()


@router.patch("/threads")
def update_threads_state(body: MailboxBulkStateIn, db: Session = Depends(get_db)):
    thread_ids = list(dict.fromkeys(body.thread_ids))
    if not thread_ids:
        raise HTTPException(422, "thread_ids must not be empty")
    threads = db.query(Thread).filter(Thread.id.in_(thread_ids)).all()
    found_ids = {thread.id for thread in threads}
    missing_ids = [thread_id for thread_id in thread_ids if thread_id not in found_ids]
    if missing_ids:
        raise HTTPException(404, {"message": "Threads not found", "thread_ids": missing_ids})
    _apply_state(db, threads, body)
    return {"thread_ids": thread_ids, "updated": len(thread_ids)}


@router.patch("/threads/{thread_id}")
def update_thread_state(
    thread_id: int,
    body: MailboxStateIn,
    db: Session = Depends(get_db),
):
    thread = db.get(Thread, thread_id)
    if not thread:
        raise HTTPException(404, "Thread not found")
    _apply_state(db, [thread], body)
    return {
        "thread_id": thread_id,
        "is_read": body.is_read,
        "is_starred": body.is_starred,
    }
