"""项目评估表 - 一个 KOL 在不同项目（Dola/Pippit）下的适配评估。

对应 alembic migration kol_v2_0001。这是 PIPPIT §4.5 project_assessment 的最小实现：
Dola 和 Pippit 是并列项目，同一 KOL 可在两项目下有各自独立的 fit_status /
core_scenario / recommend_angle / kol_category，互不覆盖。
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint

from db import Base


class ProjectAssessment(Base):
    __tablename__ = "project_assessment"
    __table_args__ = (
        UniqueConstraint("project_code", "kol_id", name="uq_project_assessment_project_kol"),
    )

    id = Column(Integer, primary_key=True, index=True)
    project_code = Column(String(30), nullable=False, comment="dola_uk / pippit_2026 等")
    kol_id = Column(Integer, ForeignKey("kol.id", ondelete="CASCADE"), nullable=False, index=True)
    fit_status = Column(String(10), comment="fit / not_fit / 空=未知")
    core_scenario = Column(String(200), comment="核心场景，如 工作场景【P1】")
    recommend_angle = Column(Text, comment="项目专属推荐内容角度")
    kol_category = Column(String(100), comment="达人画像/类别")
    collected_at = Column(DateTime, comment="评估采集时间")
    created_at = Column(DateTime, default=datetime.utcnow)
