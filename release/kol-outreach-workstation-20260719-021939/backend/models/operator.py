"""运营人员表 - 谁在看 Hot Lead、谁在回复"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Boolean

from db import Base


class Operator(Base):
    __tablename__ = "operator"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, comment="运营姓名")
    email = Column(String(200), unique=True, nullable=False, comment="登录邮箱")
    role = Column(String(50), default="operator", comment="admin/operator")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
