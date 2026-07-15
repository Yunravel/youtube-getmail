"""内部备注表 - 运营之间的私聊讨论(不发给客户)"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship

from db import Base


class Note(Base):
    __tablename__ = "note"

    id = Column(Integer, primary_key=True, index=True)
    thread_id = Column(Integer, ForeignKey("thread.id"), nullable=False, index=True)
    operator_id = Column(Integer, ForeignKey("operator.id"), nullable=False)
    content = Column(Text, nullable=False, comment="备注内容(支持@同事)")
    created_at = Column(DateTime, default=datetime.utcnow)

    operator = relationship("Operator", lazy="joined")
