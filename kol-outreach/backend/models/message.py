"""邮件消息表 - 每一封收/发的邮件"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Boolean
from sqlalchemy import JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import expression

from db import Base


class Message(Base):
    __tablename__ = "message"

    id = Column(Integer, primary_key=True, index=True)
    thread_id = Column(Integer, ForeignKey("thread.id"), nullable=False, index=True)

    # outbound=我们发出, inbound=对方回信
    direction = Column(String(10), nullable=False, comment="outbound/inbound")

    from_email = Column(String(200), nullable=False)
    to_email = Column(String(200), nullable=False)
    subject = Column(String(500))
    body_text = Column(Text)
    body_html = Column(Text)
    # Snov reply APIs do not guarantee attachment fields. When a webhook/API
    # payload includes them, keep normalized metadata here (never credentials).
    # [{"id": "...", "name": "brief.pdf", "url": "...", "size": 123,
    #   "content_type": "application/pdf"}]
    attachments = Column(JSON, default=list, comment="附件元数据列表")

    # ===== RFC 5256 邮件归组字段(关键) =====
    # 用这些做会话归组,不靠 subject 匹配
    message_id = Column(String(500), unique=True, index=True, comment="邮件唯一ID")
    # Webhook/provider IDs are not guaranteed to be valid RFC Message-IDs.
    rfc_message_id = Column(String(500), index=True, comment="RFC 822 Message-ID")
    in_reply_to = Column(String(500), comment="回复的那封的 Message-ID")
    references = Column(Text, comment="整个会话的 Message-ID 链")

    # ===== AI 分析结果(仅 inbound 回信才有) =====
    # 格式: {"intent": "high", "intent_score": 85, "summary": "...", ...}
    ai_analysis = Column(JSON, comment="GPT 意向分析 JSON")

    is_read = Column(Boolean, default=False, comment="运营是否已读")
    received_at = Column(DateTime, default=datetime.utcnow)
    # 上游没给收信时间、或给的时间解析不了时，received_at 只能填入库时刻。
    # 这种时间戳不能用来判断邮件新鲜度：补录一封一个月前的信也会显示成"刚刚"。
    # 自动回复的两小时窗口据此拒绝放行，改走人工队列。
    received_at_estimated = Column(
        Boolean, nullable=False, default=False, server_default=expression.false(),
        comment="received_at 是入库推算值而非邮件真实时间",
    )

    thread = relationship("Thread", back_populates="messages")
