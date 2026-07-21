"""create mailbox_credential table for IMAP attachment sync

Revision ID: mailbox_cred_0004
Revises: crawler_product_0003
Create Date: 2026-07-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "mailbox_cred_0004"
down_revision: Union[str, None] = "crawler_product_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Development startup uses Base.metadata.create_all(), so a hot-reloaded
    # backend may have already created this table before Alembic is run.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "mailbox_credential" not in inspector.get_table_names():
        op.create_table(
            "mailbox_credential",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("email", sa.String(length=200), nullable=False),
            sa.Column("encrypted_password", sa.Text(), nullable=False),
            sa.Column("provider", sa.String(length=30), nullable=False, server_default="gmail"),
            sa.Column("imap_host", sa.String(length=100), nullable=False, server_default="imap.gmail.com"),
            sa.Column("imap_port", sa.Integer(), nullable=False, server_default="993"),
            sa.Column("snov_id", sa.Integer(), nullable=True),
            sa.Column("last_synced_at", sa.DateTime(), nullable=True),
            sa.Column("last_sync_status", sa.String(length=20), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("email", name="uq_mailbox_credential_email"),
        )
        op.create_index("ix_mailbox_credential_id", "mailbox_credential", ["id"])
        op.create_index("ix_mailbox_credential_email", "mailbox_credential", ["email"])
    else:
        indexes = {item["name"] for item in inspector.get_indexes("mailbox_credential")}
        if "ix_mailbox_credential_id" not in indexes:
            op.create_index("ix_mailbox_credential_id", "mailbox_credential", ["id"])
        if "ix_mailbox_credential_email" not in indexes:
            op.create_index("ix_mailbox_credential_email", "mailbox_credential", ["email"])


def downgrade() -> None:
    op.drop_index("ix_mailbox_credential_email", table_name="mailbox_credential")
    op.drop_index("ix_mailbox_credential_id", table_name="mailbox_credential")
    op.drop_table("mailbox_credential")
