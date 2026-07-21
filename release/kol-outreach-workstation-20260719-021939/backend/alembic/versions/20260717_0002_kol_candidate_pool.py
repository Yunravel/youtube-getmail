"""kol_candidate 候选池表 + kol_emailable 视图

Revision ID: kol_candidate_0002
Revises: kol_v2_0001
Create Date: 2026-07-17

导入 KOL-Find 多平台候选池（5103 行）作为"大数据库"。表 A = kol_candidate
（全量候选），视图 = kol_emailable（有联系邮箱的子集，约 602 行）。

去重键：UNIQUE(platform, account) —— 实测 5103 行零重复；同人多平台各一行合法。
联系邮箱不做唯一（77 个重复邮箱 99% 是同人跨平台的合法重复）。

字段来源：KOL-Find_多平台候选池_邮箱采集结果_已修复.xlsx 的「全部候选」表 28 列，
转成英文 snake_case。粉丝数/近期平均播放/邮箱(爬虫回填) 三列源数据全空，仍建列保留
（未来采集工具回填时可直接用）；邮箱核验状态源数据恒为"待爬取"，给 DEFAULT。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "kol_candidate_0002"
down_revision: Union[str, None] = "kol_v2_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "kol_candidate",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_row", sa.Integer(), comment="原 Excel 行号，溯源用"),
        sa.Column("platform", sa.String(length=20), nullable=False, comment="YouTube/Instagram/TikTok/X"),
        sa.Column("account", sa.String(length=200), nullable=False, comment="平台账号名"),
        sa.Column("profile_url", sa.String(length=2048), comment="主页链接"),
        sa.Column("related_youtube", sa.String(length=200), comment="关联YouTube账号，软关联键识别跨平台同人"),
        sa.Column("yt_about_source", sa.String(length=2048), comment="YouTube About 来源"),
        sa.Column("discovery_method", sa.String(length=100), comment="发现方式"),
        sa.Column("fit_product", sa.String(length=100), comment="适配产品，可含逗号多值"),
        sa.Column("recommend_product", sa.String(length=50), comment="主要推荐产品"),
        sa.Column("hit_keyword_count", sa.Integer(), comment="命中检索词数"),
        sa.Column("keyword_note", sa.Text(), comment="发现关键词/种子说明"),
        sa.Column("crawl_priority", sa.String(length=20), comment="抓取优先级：最高/高/中/低"),
        sa.Column("email_crawler", sa.String(length=200), comment="邮箱(爬虫回填)，源数据目前全空"),
        sa.Column("email_source_url", sa.String(length=2048), comment="邮箱来源URL"),
        sa.Column("email_verify_status", sa.String(length=50), server_default="待爬取", comment="邮箱核验状态"),
        sa.Column("country_region", sa.String(length=100), comment="国家/地区"),
        sa.Column("language", sa.String(length=50), comment="语言"),
        sa.Column("account_type", sa.String(length=50), comment="账号类型"),
        sa.Column("followers", sa.BigInteger(), comment="粉丝数，源数据目前全空"),
        sa.Column("avg_views", sa.BigInteger(), comment="近期平均播放，源数据目前全空"),
        sa.Column("review_status", sa.String(length=50), comment="人工复核状态"),
        sa.Column("remark", sa.Text(), comment="备注"),
        sa.Column("contact_email", sa.String(length=500), comment="联系邮箱，602行有值，61行|分隔多值"),
        sa.Column("email_status", sa.String(length=50), comment="邮箱状态：已获取/未获取/需登录验证码"),
        sa.Column("email_source", sa.String(length=100), comment="邮箱来源"),
        sa.Column("other_links", sa.Text(), comment="其他链接，|分隔多值保留原串"),
        sa.Column("collect_status", sa.String(length=100), comment="采集状态"),
        sa.Column("collected_at", sa.DateTime(timezone=True), comment="采集时间"),
        sa.Column("import_batch", sa.String(length=100), comment="导入批次标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("platform", "account", name="uq_kol_candidate_platform_account"),
    )
    op.create_index("ix_kol_candidate_contact_email", "kol_candidate", ["contact_email"])
    op.create_index("ix_kol_candidate_platform_priority", "kol_candidate", ["platform", "crawl_priority"])
    op.create_index("ix_kol_candidate_related_youtube", "kol_candidate", ["related_youtube"])

    # 视图：有联系邮箱的子集（表 B）。PostgreSQL/SQLite 通用语法。
    op.execute(
        "CREATE VIEW kol_emailable AS "
        "SELECT * FROM kol_candidate "
        "WHERE contact_email IS NOT NULL AND contact_email <> ''"
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS kol_emailable")
    op.drop_index("ix_kol_candidate_related_youtube", table_name="kol_candidate")
    op.drop_index("ix_kol_candidate_platform_priority", table_name="kol_candidate")
    op.drop_index("ix_kol_candidate_contact_email", table_name="kol_candidate")
    op.drop_table("kol_candidate")
