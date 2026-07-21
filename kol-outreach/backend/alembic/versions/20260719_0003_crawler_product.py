"""persist custom crawler products

Revision ID: crawler_product_0003
Revises: kol_candidate_0002
Create Date: 2026-07-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "crawler_product_0003"
down_revision: Union[str, None] = "kol_candidate_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Development startup uses Base.metadata.create_all(), so a hot-reloaded
    # backend may have already created this table before Alembic is run.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "crawler_product" not in inspector.get_table_names():
        op.create_table(
            "crawler_product",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=50), nullable=False),
            sa.Column("name_normalized", sa.String(length=50), nullable=False),
            sa.Column("keywords", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("name_normalized", name="uq_crawler_product_name_normalized"),
        )
        op.create_index("ix_crawler_product_id", "crawler_product", ["id"])
    else:
        indexes = {item["name"] for item in inspector.get_indexes("crawler_product")}
        if "ix_crawler_product_id" not in indexes:
            op.create_index("ix_crawler_product_id", "crawler_product", ["id"])


def downgrade() -> None:
    op.drop_index("ix_crawler_product_id", table_name="crawler_product")
    op.drop_table("crawler_product")
