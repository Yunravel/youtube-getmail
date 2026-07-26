"""Durable one-KOL-one-row Feishu synchronization task."""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from db import Base


class FeishuSyncTask(Base):
    __tablename__ = "feishu_sync_task"

    id = Column(Integer, primary_key=True, index=True)
    kol_id = Column(
        Integer,
        ForeignKey("kol.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    source_message_id = Column(
        Integer,
        ForeignKey("message.id"),
        nullable=False,
        index=True,
    )
    status = Column(String(30), nullable=False, default="pending", index=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    next_retry_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    last_error = Column(Text)
    payload_hash = Column(String(64))
    feishu_row_number = Column(Integer)
    synced_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    kol = relationship("Kol")
    source_message = relationship("Message")
