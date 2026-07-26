"""mark messages whose received_at is an ingest-time estimate

Revision ID: received_at_prov_0008
Revises: kol_email_fix_0007
Create Date: 2026-07-27

背景：webhook / Snov 轮询 / IMAP 补录三条入库通道在上游缺时间戳或时间戳解析失败时，
都会把 ``received_at`` 填成入库时刻。这样一封一个月前的历史回信入库后看起来"刚收到"，
自动回复的两小时新鲜度窗口据此放行，会对陈旧邮件真的发信出去。

本列把"推算值"与"邮件真实时间"区分开：置位的消息不进自动发送队列，改走人工复核。
既有数据一律按 false 处理——历史行无法回溯判断，且它们的两小时窗口早已过期，
不会因此产生新的自动发送。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "received_at_prov_0008"
down_revision: Union[str, None] = "kol_email_fix_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "message",
        sa.Column(
            "received_at_estimated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("message", "received_at_estimated")
