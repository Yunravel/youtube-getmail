"""邮件会话表 - 一个 KOL 一个 thread,聚合往来邮件"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship

from db import Base


class Thread(Base):
    __tablename__ = "thread"

    id = Column(Integer, primary_key=True, index=True)
    kol_id = Column(Integer, ForeignKey("kol.id"), nullable=False, index=True)
    subject = Column(String(500), comment="会话主题(首封主题)")
    # Snov Campaign 归属：同一联系人参加不同营销活动时不能混在一起。
    campaign_id = Column(String(100), index=True, comment="Snov Campaign ID")
    campaign_name = Column(String(300), comment="Snov Campaign 名称")

    # 状态机:
    #   open      - 已发出,等回复
    #   hot       - AI 判定高意向,推到看板顶部
    #   warming   - 中等意向
    #   cooling   - 低意向
    #   closed    - 已结束(合作/拒绝/放弃)
    status = Column(String(50), default="open", index=True)

    # 分配给哪个运营
    assignee_id = Column(Integer, ForeignKey("operator.id"), nullable=True, index=True)

    # AI 意向分析结果(每收到一封回信更新)
    last_intent = Column(String(50), comment="high/medium/low/negative/ooo/auto")
    intent_score = Column(Integer, default=0, comment="0-100,越高越热")
    ai_summary = Column(Text, comment="AI 一句话总结")

    reply_count = Column(Integer, default=0, comment="收到回信数")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系
    kol = relationship("Kol", lazy="joined")
    assignee = relationship("Operator", lazy="joined")
    messages = relationship(
        "Message",
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="Message.received_at",
    )
