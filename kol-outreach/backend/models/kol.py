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
    email = Column(String(200), nullable=False, index=True, comment="联系邮箱（唯一识别）")

    # Snov 联系人导入模板字段。保留上面的 name/channel_* 等旧字段，确保现有
    # 看板、会话和爬虫 CSV 无需一次性改造。
    snov_prospect_id = Column(String(200), index=True, comment="Snov prospect ID")
    full_name = Column(String(300), comment="Snov fullName")
    first_name = Column(String(150), comment="Snov firstName")
    last_name = Column(String(150), comment="Snov lastName")
    locality = Column(String(150), comment="Snov city/locality")
    position = Column(String(200), comment="职位或创作者身份")
    company_name = Column(String(300), comment="公司、频道或工作室")
    company_site = Column(String(500), comment="完整公司网址")
    phones = Column(String(100), comment="含国家区号的单个电话号码")
    linkedin_url = Column(String(500), comment="LinkedIn 个人主页")
    platform = Column(String(50), comment="YouTube/Instagram/TikTok/LinkedIn/Other")
    social_handle = Column(String(200), comment="社交账号名")
    profile_url = Column(String(500), comment="创作者主页完整链接")
    followers = Column(Integer, default=0, comment="粉丝数")
    priority = Column(String(2), comment="P0/P1/P2/P3")
    content_category = Column(String(150), comment="内容领域")
    source = Column(String(200), comment="数据来源")
    contact_notes = Column(Text, comment="联系人简短备注")
    snov_list_id = Column(String(100), index=True, comment="Snov 主名单 ID")
    snov_list_name = Column(String(300), comment="Snov 主名单名称")
    snov_list_ids = Column(JSON, comment="联系人所属的全部 Snov 名单 ID")
    snov_custom_fields = Column(JSON, comment="Snov 未映射的自定义字段")
    snov_raw_data = Column(JSON, comment="最后一次 Snov 原始联系人响应")
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
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
