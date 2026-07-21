"""KOL 邮箱表 - 一个 KOL 可有多个邮箱（主邮箱 + 备用/商务邮箱）。

对应 alembic migration kol_v2_0001。解决旧 kol.email 单列存不下多邮箱的问题：
导入时联系邮箱若有多个（用 , ; | 分隔），主邮箱写 kol.email（兼容旧字段），
全部邮箱逐条写入本表，is_primary 标记主邮箱。
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from db import Base


class KolEmail(Base):
    __tablename__ = "kol_email"
    __table_args__ = (
        UniqueConstraint("kol_id", "email_normalized", name="uq_kol_email_kol_normalized"),
    )

    id = Column(Integer, primary_key=True, index=True)
    kol_id = Column(Integer, ForeignKey("kol.id", ondelete="CASCADE"), nullable=False)
    email = Column(String(200), nullable=False, comment="原始邮箱")
    email_normalized = Column(String(200), nullable=False, index=True, comment="小写规范化邮箱，唯一性判断依据")
    is_primary = Column(Boolean, nullable=False, default=False, comment="是否主邮箱（发信用）")
    source = Column(String(200), comment="邮箱来源")
    created_at = Column(DateTime, default=datetime.utcnow)
