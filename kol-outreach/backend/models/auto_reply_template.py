"""Campaign-scoped templates for quote acknowledgement replies."""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from db import Base


DEFAULT_SUBJECT = "Re: {{ original_subject }}"
DEFAULT_BODY = """Hi {{ creator_name }},

Thank you for sharing your rates. I've noted the following:
{{ quote_summary }}

{{ question_response }}

We'll review this internally and get back to you with next steps.

Best,
{{ signature }}"""


class AutoReplyTemplate(Base):
    __tablename__ = "auto_reply_template"

    id = Column(Integer, primary_key=True, index=True)
    scope_key = Column(String(200), nullable=False, unique=True, index=True)
    campaign_id = Column(String(100), nullable=True, index=True)
    campaign_name = Column(String(300), nullable=True)
    enabled = Column(Boolean, nullable=False, default=False)
    auto_send_enabled = Column(Boolean, nullable=False, default=False)
    subject_template = Column(Text, nullable=False, default=DEFAULT_SUBJECT)
    body_template = Column(Text, nullable=False, default=DEFAULT_BODY)
    campaign_context = Column(Text, nullable=True)
    ai_instructions = Column(Text, nullable=True)
    signature = Column(Text, nullable=False, default="Partnership Team")
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
