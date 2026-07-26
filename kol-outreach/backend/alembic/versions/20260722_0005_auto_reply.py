"""add durable quote auto replies

Revision ID: auto_reply_0005
Revises: mailbox_cred_0004
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "auto_reply_0005"
down_revision: Union[str, None] = "mailbox_cred_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("message", sa.Column("rfc_message_id", sa.String(500), nullable=True))
    op.create_index("ix_message_rfc_message_id", "message", ["rfc_message_id"])
    op.add_column("mailbox_credential", sa.Column("smtp_host", sa.String(100), nullable=False, server_default="smtp.gmail.com"))
    op.add_column("mailbox_credential", sa.Column("smtp_port", sa.Integer(), nullable=False, server_default="465"))
    op.add_column("mailbox_credential", sa.Column("smtp_use_ssl", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("mailbox_credential", sa.Column("smtp_verified_at", sa.DateTime(), nullable=True))
    op.add_column("mailbox_credential", sa.Column("smtp_last_error", sa.Text(), nullable=True))

    op.create_table(
        "auto_reply_template",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope_key", sa.String(200), nullable=False),
        sa.Column("campaign_id", sa.String(100), nullable=True),
        sa.Column("campaign_name", sa.String(300), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auto_send_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("subject_template", sa.Text(), nullable=False),
        sa.Column("body_template", sa.Text(), nullable=False),
        sa.Column("campaign_context", sa.Text(), nullable=True),
        sa.Column("ai_instructions", sa.Text(), nullable=True),
        sa.Column("signature", sa.Text(), nullable=False, server_default="Partnership Team"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("scope_key", name="uq_auto_reply_template_scope_key"),
    )
    op.create_index("ix_auto_reply_template_id", "auto_reply_template", ["id"])
    op.create_index("ix_auto_reply_template_scope_key", "auto_reply_template", ["scope_key"], unique=True)
    op.create_index("ix_auto_reply_template_campaign_id", "auto_reply_template", ["campaign_id"])

    op.create_table(
        "scheduled_reply",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("thread_id", sa.Integer(), sa.ForeignKey("thread.id"), nullable=False),
        sa.Column("source_message_id", sa.Integer(), sa.ForeignKey("message.id"), nullable=False),
        sa.Column("mailbox_credential_id", sa.Integer(), sa.ForeignKey("mailbox_credential.id"), nullable=True),
        sa.Column("template_id", sa.Integer(), sa.ForeignKey("auto_reply_template.id"), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="queued"),
        sa.Column("quote_snapshot", sa.JSON(), nullable=True),
        sa.Column("template_snapshot", sa.JSON(), nullable=True),
        sa.Column("draft_subject", sa.Text(), nullable=True),
        sa.Column("draft_body", sa.Text(), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(), nullable=True),
        sa.Column("deadline_at", sa.DateTime(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("manual_override", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("outbound_message_id", sa.Integer(), sa.ForeignKey("message.id"), nullable=True),
        sa.Column("edited_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("source_message_id", name="uq_scheduled_reply_source_message_id"),
    )
    for name, columns in (
        ("ix_scheduled_reply_id", ["id"]),
        ("ix_scheduled_reply_thread_id", ["thread_id"]),
        ("ix_scheduled_reply_source_message_id", ["source_message_id"]),
        ("ix_scheduled_reply_mailbox_credential_id", ["mailbox_credential_id"]),
        ("ix_scheduled_reply_status", ["status"]),
        ("ix_scheduled_reply_scheduled_at", ["scheduled_at"]),
    ):
        op.create_index(name, "scheduled_reply", columns, unique=name == "ix_scheduled_reply_source_message_id")


def downgrade() -> None:
    op.drop_table("scheduled_reply")
    op.drop_table("auto_reply_template")
    op.drop_index("ix_message_rfc_message_id", table_name="message")
    op.drop_column("message", "rfc_message_id")
    for column in ("smtp_last_error", "smtp_verified_at", "smtp_use_ssl", "smtp_port", "smtp_host"):
        op.drop_column("mailbox_credential", column)
