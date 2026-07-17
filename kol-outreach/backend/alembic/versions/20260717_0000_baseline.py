"""baseline: 引入 alembic 前的现有 schema 快照

Revision ID: baseline_0000
Revises:
Create Date: 2026-07-17

这是 alembic 基线——把引入 alembic 之前（2026-07）由 ``create_all`` +
``_ensure_snov_contact_schema`` / ``_ensure_mailbox_schema`` 累积形成的现有 schema
固化下来。它包含 6 张业务表及其所有历史补列（Snov 联系人字段、mailbox 字段等），
对应生产库实测的 36 列 kol 表。

部署用法：
  - 新空库：``alembic upgrade head`` 会先跑 baseline（建 6 表）再跑 kol_v2_0001。
  - 现有生产库（已具备这些表）：``alembic stamp baseline_0000`` 把当前库标记为基线，
    然后 ``alembic upgrade head`` 只执行 kol_v2_0001 的加列/建表/回填。

注意：本 baseline 不含 Tier 1 新增的 8 列和 2 张新表（它们在 kol_v2_0001）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "baseline_0000"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- operator ----
    op.create_table(
        "operator",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=50)),
        sa.Column("is_active", sa.Boolean()),
        sa.Column("created_at", sa.DateTime()),
        sa.UniqueConstraint("email", name="uq_operator_email"),
    )
    op.create_index("ix_operator_id", "operator", ["id"])

    # ---- kol（含 _ensure_snov_contact_schema 补的全部列，36 列）----
    op.create_table(
        "kol",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("channel_id", sa.String(length=100)),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("channel_url", sa.String(length=500)),
        sa.Column("email", sa.String(length=200)),
        sa.Column("subscribers", sa.Integer()),
        sa.Column("country", sa.String(length=50)),
        sa.Column("niche", sa.String(length=100)),
        sa.Column("recent_videos", sa.JSON()),
        sa.Column("personal_intro", sa.Text()),
        sa.Column("status", sa.String(length=50)),
        sa.Column("created_at", sa.DateTime()),
        # 旧爬虫 schema 到此；以下为 _ensure_snov_contact_schema 补的 Snov 字段
        sa.Column("snov_prospect_id", sa.String(length=200)),
        sa.Column("full_name", sa.String(length=300)),
        sa.Column("first_name", sa.String(length=150)),
        sa.Column("last_name", sa.String(length=150)),
        sa.Column("locality", sa.String(length=150)),
        sa.Column("position", sa.String(length=200)),
        sa.Column("company_name", sa.String(length=300)),
        sa.Column("company_site", sa.String(length=500)),
        sa.Column("phones", sa.String(length=100)),
        sa.Column("linkedin_url", sa.String(length=500)),
        sa.Column("platform", sa.String(length=50)),
        sa.Column("social_handle", sa.String(length=200)),
        sa.Column("profile_url", sa.String(length=500)),
        sa.Column("followers", sa.Integer()),
        sa.Column("priority", sa.String(length=2)),
        sa.Column("content_category", sa.String(length=150)),
        sa.Column("source", sa.String(length=200)),
        sa.Column("contact_notes", sa.Text()),
        sa.Column("snov_list_id", sa.String(length=100)),
        sa.Column("snov_list_name", sa.String(length=300)),
        sa.Column("snov_list_ids", sa.JSON()),
        sa.Column("snov_custom_fields", sa.JSON()),
        sa.Column("snov_raw_data", sa.JSON()),
        sa.Column("updated_at", sa.DateTime()),
    )
    op.create_index("ix_kol_id", "kol", ["id"])
    op.create_index("ix_kol_channel_id", "kol", ["channel_id"])
    op.create_index("ix_kol_email", "kol", ["email"])
    op.create_index("ix_kol_status", "kol", ["status"])
    op.create_index("ix_kol_snov_prospect_id", "kol", ["snov_prospect_id"])
    op.create_index("ix_kol_snov_list_id", "kol", ["snov_list_id"])
    # 邮箱规范化唯一索引（_ensure_snov_contact_schema 里建的）
    op.create_index(
        "ux_kol_email_normalized", "kol",
        [sa.text("lower(trim(email))")],
        postgresql_where=sa.text("email IS NOT NULL AND trim(email) <> ''"),
        sqlite_where=sa.text("email IS NOT NULL AND trim(email) <> ''"),
    )

    # ---- thread（含 _ensure_mailbox_schema 补的 is_starred）----
    op.create_table(
        "thread",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kol_id", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(length=500)),
        sa.Column("campaign_id", sa.String(length=100)),
        sa.Column("campaign_name", sa.String(length=300)),
        sa.Column("status", sa.String(length=50)),
        sa.Column("assignee_id", sa.Integer()),
        sa.Column("is_starred", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_intent", sa.String(length=50)),
        sa.Column("intent_score", sa.Integer()),
        sa.Column("ai_summary", sa.Text()),
        sa.Column("reply_count", sa.Integer()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
        sa.ForeignKeyConstraint(["kol_id"], ["kol.id"]),
        sa.ForeignKeyConstraint(["assignee_id"], ["operator.id"]),
    )
    op.create_index("ix_thread_id", "thread", ["id"])
    op.create_index("ix_thread_kol_id", "thread", ["kol_id"])
    op.create_index("ix_thread_campaign_id", "thread", ["campaign_id"])
    op.create_index("ix_thread_status", "thread", ["status"])
    op.create_index("ix_thread_assignee_id", "thread", ["assignee_id"])
    op.create_index("ix_thread_is_starred", "thread", ["is_starred"])

    # ---- message（含 _ensure_mailbox_schema 补的 attachments）----
    op.create_table(
        "message",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("thread_id", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("from_email", sa.String(length=200), nullable=False),
        sa.Column("to_email", sa.String(length=200), nullable=False),
        sa.Column("subject", sa.String(length=500)),
        sa.Column("body_text", sa.Text()),
        sa.Column("body_html", sa.Text()),
        sa.Column("attachments", sa.JSON()),
        sa.Column("message_id", sa.String(length=500)),
        sa.Column("in_reply_to", sa.String(length=500)),
        sa.Column("references", sa.Text()),
        sa.Column("ai_analysis", sa.JSON()),
        sa.Column("is_read", sa.Boolean()),
        sa.Column("received_at", sa.DateTime()),
        sa.ForeignKeyConstraint(["thread_id"], ["thread.id"]),
        sa.UniqueConstraint("message_id", name="uq_message_message_id"),
    )
    op.create_index("ix_message_id", "message", ["id"])
    op.create_index("ix_message_thread_id", "message", ["thread_id"])
    op.create_index("ix_message_message_id", "message", ["message_id"])
    op.create_index("ix_message_direction_received", "message", ["direction", "received_at"])
    op.create_index("ix_message_thread_read", "message", ["thread_id", "is_read"])

    # ---- note ----
    op.create_table(
        "note",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("thread_id", sa.Integer(), nullable=False),
        sa.Column("operator_id", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime()),
        sa.ForeignKeyConstraint(["thread_id"], ["thread.id"]),
        sa.ForeignKeyConstraint(["operator_id"], ["operator.id"]),
    )
    op.create_index("ix_note_id", "note", ["id"])
    op.create_index("ix_note_thread_id", "note", ["thread_id"])

    # ---- send_log ----
    op.create_table(
        "send_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("thread_id", sa.Integer()),
        sa.Column("kol_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=50)),
        sa.Column("provider_campaign_id", sa.String(length=200)),
        sa.Column("provider_lead_id", sa.String(length=200)),
        sa.Column("status", sa.String(length=50)),
        sa.Column("error_message", sa.String(length=500)),
        sa.Column("sent_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime()),
        sa.ForeignKeyConstraint(["thread_id"], ["thread.id"]),
        sa.ForeignKeyConstraint(["kol_id"], ["kol.id"]),
    )
    op.create_index("ix_send_log_id", "send_log", ["id"])
    op.create_index("ix_send_log_thread_id", "send_log", ["thread_id"])
    op.create_index("ix_send_log_kol_id", "send_log", ["kol_id"])


def downgrade() -> None:
    # 基线回滚：drop 全部 6 张表（逆序，尊重 FK）。
    op.drop_table("send_log")
    op.drop_table("note")
    op.drop_table("message")
    op.drop_table("thread")
    op.drop_table("kol")
    op.drop_table("operator")
