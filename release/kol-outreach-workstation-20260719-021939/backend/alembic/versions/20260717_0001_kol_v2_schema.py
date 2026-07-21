"""kol v2 schema: 拆 contact_notes + project_assessment + kol_email

Revision ID: kol_v2_0001
Revises:
Create Date: 2026-07-17

这是引入 Alembic 后的第一个 migration。它不改动任何现有列（纯加法）：
  1. 给 kol 加 8 个结构化列（从 contact_notes 拆出的高价值字段）。
  2. 新建 project_assessment 表（Dola/Pippit 并列项目评估，PIPPIT §4.5 最小实现）。
  3. 新建 kol_email 表（多邮箱；主 email 由 contact_notes 备用邮箱行回填）。
  4. 回填：解析 78 行 contact_notes，把值复制到新列/新表。
     contact_notes 列本身保持原样（作为只读历史快照，详见 DATA_CONSTRAINTS §5）。

基线说明：生产库现有 36 列 schema 由本 migration 的 upgrade() 在其之上加列。
部署时对现有库执行 ``alembic upgrade head`` 即可；无需先 stamp，因为这是第一条 revision
（down_revision=None），upgrade head 会从空版本直接应用它。若库已有 alembic_version
表且为空，等价于从 base 升级。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "kol_v2_0001"
down_revision: Union[str, None] = "baseline_0000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- 1. kol 加 8 列（全部 nullable，不破坏现有数据）----
    op.add_column("kol", sa.Column("avg_views_10d", sa.Integer(), nullable=True, comment="10天平均浏览量；未知为 NULL，0 表示观测值确为 0"))
    op.add_column("kol", sa.Column("language", sa.String(length=50), nullable=True, comment="BCP47 语言码，如 en"))
    op.add_column("kol", sa.Column("email_status", sa.String(length=50), nullable=True, comment="采集邮箱状态原文，如 已获取/需人工验证/未发现"))
    op.add_column("kol", sa.Column("email_source", sa.String(length=100), nullable=True, comment="邮箱来源，如 公开主页简介"))
    op.add_column("kol", sa.Column("collect_status", sa.String(length=50), nullable=True, comment="采集状态，如 成功/需要登录验证"))
    op.add_column("kol", sa.Column("collect_at", sa.DateTime(timezone=True), nullable=True, comment="采集时间 UTC"))
    op.add_column("kol", sa.Column("source_link", sa.String(length=2048), nullable=True, comment="采集来源链接"))
    op.add_column("kol", sa.Column("fit_project_code", sa.String(length=30), nullable=True, comment="最近一次评估的项目代码；权威在 project_assessment"))

    # 便利索引：按语言、邮箱状态筛选（常见查询）
    op.create_index("ix_kol_language", "kol", ["language"])
    op.create_index("ix_kol_email_status", "kol", ["email_status"])

    # ---- 2. project_assessment 表 ----
    op.create_table(
        "project_assessment",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_code", sa.String(length=30), nullable=False, comment="dola_uk / pippit_2026 等"),
        sa.Column("kol_id", sa.Integer(), nullable=False, comment="FK kol.id"),
        sa.Column("fit_status", sa.String(length=10), nullable=True, comment="fit / not_fit / 空=未知"),
        sa.Column("core_scenario", sa.String(length=200), nullable=True, comment="核心场景，如 工作场景【P1】"),
        sa.Column("recommend_angle", sa.Text(), nullable=True, comment="项目专属推荐内容角度"),
        sa.Column("kol_category", sa.String(length=100), nullable=True, comment="达人画像/类别"),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True, comment="评估采集时间"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_code", "kol_id", name="uq_project_assessment_project_kol"),
        sa.ForeignKeyConstraint(["kol_id"], ["kol.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "fit_status IN ('fit','not_fit')",
            name="ck_project_assessment_fit_status"
        ),
    )
    op.create_index("ix_project_assessment_kol_id", "project_assessment", ["kol_id"])
    op.create_index("ix_project_assessment_project_fit", "project_assessment", ["project_code", "fit_status"])

    # ---- 3. kol_email 表 ----
    op.create_table(
        "kol_email",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kol_id", sa.Integer(), nullable=False, comment="FK kol.id"),
        sa.Column("email", sa.String(length=200), nullable=False, comment="原始邮箱"),
        sa.Column("email_normalized", sa.String(length=200), nullable=False, comment="小写规范化邮箱，唯一性判断依据"),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false(), comment="是否主邮箱（发信用）"),
        sa.Column("source", sa.String(length=200), nullable=True, comment="邮箱来源"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("kol_id", "email_normalized", name="uq_kol_email_kol_normalized"),
        sa.ForeignKeyConstraint(["kol_id"], ["kol.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_kol_email_normalized", "kol_email", ["email_normalized"])

    # ---- 4. 回填：解析 contact_notes 复制到新列/新表 ----
    # 用 raw connection 调用 data_migration，避开 ORM（此时新模型可能尚未加载）。
    from migrations.data_migration import upgrade_backfill

    bind = op.get_bind()
    report = upgrade_backfill(bind)
    # 把回填摘要写进 alembic 日志，便于审计
    print(f"[kol_v2_0001] 回填完成: {report}")


def downgrade() -> None:
    # 先删表（含 FK），再删列。contact_notes 未改动，回滚无损。
    op.drop_index("ix_kol_email_normalized", table_name="kol_email")
    op.drop_table("kol_email")

    op.drop_index("ix_project_assessment_project_fit", table_name="project_assessment")
    op.drop_index("ix_project_assessment_kol_id", table_name="project_assessment")
    op.drop_table("project_assessment")

    op.drop_index("ix_kol_email_status", table_name="kol")
    op.drop_index("ix_kol_language", table_name="kol")
    for col in (
        "fit_project_code",
        "source_link",
        "collect_at",
        "collect_status",
        "email_source",
        "email_status",
        "language",
        "avg_views_10d",
    ):
        op.drop_column("kol", col)
