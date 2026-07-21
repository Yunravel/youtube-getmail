"""发送日志表 - 记录通过 Instantly 发的每一封"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey

from db import Base


class SendLog(Base):
    __tablename__ = "send_log"

    id = Column(Integer, primary_key=True, index=True)
    thread_id = Column(Integer, ForeignKey("thread.id"), nullable=True, index=True)
    kol_id = Column(Integer, ForeignKey("kol.id"), nullable=False, index=True)

    provider = Column(String(50), default="instantly", comment="发信平台")
    provider_campaign_id = Column(String(200), comment="Instantly campaign ID")
    provider_lead_id = Column(String(200), comment="Instantly lead ID")

    # pending / sent / failed / bounced
    status = Column(String(50), default="pending")
    error_message = Column(String(500))
    sent_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
