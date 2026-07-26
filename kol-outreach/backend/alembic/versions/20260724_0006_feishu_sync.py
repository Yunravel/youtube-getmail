"""add durable Feishu synchronization tasks

Revision ID: feishu_sync_0006
Revises: auto_reply_0005
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "feishu_sync_0006"
down_revision: Union[str, None] = "auto_reply_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feishu_sync_task",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kol_id", sa.Integer(), sa.ForeignKey("kol.id"), nullable=False),
        sa.Column(
            "source_message_id",
            sa.Integer(),
            sa.ForeignKey("message.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("payload_hash", sa.String(64), nullable=True),
        sa.Column("feishu_row_number", sa.Integer(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("kol_id", name="uq_feishu_sync_task_kol_id"),
    )
    for name, columns, unique in (
        ("ix_feishu_sync_task_id", ["id"], False),
        ("ix_feishu_sync_task_kol_id", ["kol_id"], True),
        ("ix_feishu_sync_task_source_message_id", ["source_message_id"], False),
        ("ix_feishu_sync_task_status", ["status"], False),
        ("ix_feishu_sync_task_next_retry_at", ["next_retry_at"], False),
    ):
        op.create_index(name, "feishu_sync_task", columns, unique=unique)


def downgrade() -> None:
    op.drop_table("feishu_sync_task")
