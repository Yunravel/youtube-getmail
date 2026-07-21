"""Persisted user-defined product and crawler keyword groups."""
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, JSON, String, UniqueConstraint

from db import Base


class CrawlerProduct(Base):
    __tablename__ = "crawler_product"
    __table_args__ = (
        UniqueConstraint("name_normalized", name="uq_crawler_product_name_normalized"),
    )

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False)
    name_normalized = Column(String(50), nullable=False)
    keywords = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
