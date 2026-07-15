"""KOL 主表 - 爬虫产出的目标博主"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Text

# JSONB:PostgreSQL 用;SQLite 回退到 JSON
from sqlalchemy import JSON
from db import Base


class Kol(Base):
    __tablename__ = "kol"

    id = Column(Integer, primary_key=True, index=True)
    channel_id = Column(String(100), index=True, comment="YouTube channel ID")
    name = Column(String(200), nullable=False, comment="博主名")
    channel_url = Column(String(500), comment="频道链接")
    email = Column(String(200), index=True, comment="联系邮箱")
    subscribers = Column(Integer, default=0, comment="粉丝数")
    country = Column(String(50), comment="国家")
    niche = Column(String(100), comment="赛道:tech/beauty/gaming...")

    # 最近视频标题列表,给 GPT 写个性化开场白用
    # 格式: ["标题1", "标题2", ...]
    recent_videos = Column(JSON, comment="最近视频标题列表")

    # GPT 生成的个性化开场白(发首封邮件用)
    personal_intro = Column(Text, comment="AI生成的个性化开场白")

    # 状态:pending(待发) / sent(已发首封) / in_conversation(对话中)
    #       closed(结束) / blacklisted(无效)
    status = Column(String(50), default="pending", index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
