"""Durable auto-reply queue. Drafts are not sent messages."""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text

from db import Base


class ScheduledReply(Base):
    __tablename__ = "scheduled_reply"

    id = Column(Integer, primary_key=True, index=True)
    thread_id = Column(Integer, ForeignKey("thread.id"), nullable=False, index=True)
    source_message_id = Column(Integer, ForeignKey("message.id"), nullable=False, unique=True, index=True)
    mailbox_credential_id = Column(Integer, ForeignKey("mailbox_credential.id"), nullable=True, index=True)
    template_id = Column(Integer, ForeignKey("auto_reply_template.id"), nullable=True)
    status = Column(String(30), nullable=False, default="queued", index=True)
    quote_snapshot = Column(JSON, nullable=True)
    template_snapshot = Column(JSON, nullable=True)
    draft_subject = Column(Text, nullable=True)
    draft_body = Column(Text, nullable=True)
    scheduled_at = Column(DateTime, nullable=True, index=True)
    deadline_at = Column(DateTime, nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    manual_override = Column(Boolean, nullable=False, default=False)
    error_message = Column(Text, nullable=True)
    outbound_message_id = Column(Integer, ForeignKey("message.id"), nullable=True)
    edited_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
