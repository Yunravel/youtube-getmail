"""KOL 候选池表 - KOL-Find 多平台候选池的完整 copy（"大数据库"）。

对应 alembic migration kol_candidate_0002。5103 行全量候选，含无邮箱的。
有联系邮箱的子集通过视图 ``kol_emailable`` 暴露。

去重：UNIQUE(platform, account)。同人多平台各一行合法。
粉丝数/avg_views/email_crawler 源数据全空，建列保留供未来回填。
"""
from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Integer, String, Text, UniqueConstraint

from db import Base


class KolCandidate(Base):
    __tablename__ = "kol_candidate"
    __table_args__ = (
        UniqueConstraint("platform", "account", name="uq_kol_candidate_platform_account"),
    )

    id = Column(Integer, primary_key=True, index=True)
    source_row = Column(Integer, comment="原 Excel 行号")
    platform = Column(String(20), nullable=False, comment="YouTube/Instagram/TikTok/X")
    account = Column(String(200), nullable=False, comment="平台账号名")
    profile_url = Column(String(2048), comment="主页链接")
    related_youtube = Column(String(200), index=True, comment="关联YouTube账号，软关联键")
    yt_about_source = Column(String(2048))
    discovery_method = Column(String(100))
    fit_product = Column(String(100), comment="适配产品，可含逗号多值")
    recommend_product = Column(String(50))
    hit_keyword_count = Column(Integer)
    keyword_note = Column(Text)
    crawl_priority = Column(String(20), comment="最高/高/中/低")
    email_crawler = Column(String(200), comment="源数据目前全空")
    email_source_url = Column(String(2048))
    email_verify_status = Column(String(50), default="待爬取")
    country_region = Column(String(100))
    language = Column(String(50))
    account_type = Column(String(50))
    followers = Column(BigInteger, comment="源数据目前全空")
    avg_views = Column(BigInteger, comment="源数据目前全空")
    review_status = Column(String(50))
    remark = Column(Text)
    contact_email = Column(String(500), index=True, comment="602行有值，61行|分隔多值")
    email_status = Column(String(50))
    email_source = Column(String(100))
    other_links = Column(Text, comment="|分隔多值保留原串")
    collect_status = Column(String(100))
    collected_at = Column(DateTime)
    import_batch = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
