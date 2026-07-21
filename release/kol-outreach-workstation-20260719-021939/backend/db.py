"""
KOL 外联中台 - 数据库连接与 Session 管理
SQLAlchemy 2.0 风格
"""
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from config import settings

# SQLite 需要开 check_same_thread=False 才能在 FastAPI 多线程用
connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    echo=False,              # 调试时可开 True 打印 SQL
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 所有 ORM 模型的基类
Base = declarative_base()


def get_db():
    """FastAPI 依赖注入:每个请求拿一个独立 session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """初始化数据库 - 建表(开发用,生产用 alembic 迁移)。

    历史背景：本项目长期用 ``create_all`` + 启动时 ``ALTER TABLE`` 维护 schema，
    生产库靠 ``_ensure_*`` 函数补列。自引入 alembic（2026-07）后，生产 schema 变更
    应走 ``alembic upgrade head``；此处仅保留给本地 SQLite 开发快速建表，避免回归。
    新增列/表请写到 alembic migration，不要在这里继续堆 ``_ensure_*``。
    """
    # 必须先 import 所有模型,确保它们注册到 Base.metadata
    import models  # noqa: F401  触发模型注册
    Base.metadata.create_all(bind=engine)
    _ensure_snov_contact_schema()
    _ensure_mailbox_schema()


def _ensure_snov_contact_schema():
    """Expand old KOL databases to the Snov contact-template schema."""
    inspector = inspect(engine)
    if "kol" not in set(inspector.get_table_names()):
        return

    columns = {column["name"] for column in inspector.get_columns("kol")}
    additions = {
        "snov_prospect_id": "VARCHAR(200)",
        "full_name": "VARCHAR(300)",
        "first_name": "VARCHAR(150)",
        "last_name": "VARCHAR(150)",
        "locality": "VARCHAR(150)",
        "position": "VARCHAR(200)",
        "company_name": "VARCHAR(300)",
        "company_site": "VARCHAR(500)",
        "phones": "VARCHAR(100)",
        "linkedin_url": "VARCHAR(500)",
        "platform": "VARCHAR(50)",
        "social_handle": "VARCHAR(200)",
        "profile_url": "VARCHAR(500)",
        "followers": "INTEGER DEFAULT 0",
        "priority": "VARCHAR(2)",
        "content_category": "VARCHAR(150)",
        "source": "VARCHAR(200)",
        "contact_notes": "TEXT",
        "snov_list_id": "VARCHAR(100)",
        "snov_list_name": "VARCHAR(300)",
        "snov_list_ids": "JSON",
        "snov_custom_fields": "JSON",
        "snov_raw_data": "JSON",
        "updated_at": "TIMESTAMP",
    }
    with engine.begin() as connection:
        for name, sql_type in additions.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE kol ADD COLUMN {name} {sql_type}"))

        # Backfill template fields from the original KOL crawler schema.
        connection.execute(text(
            "UPDATE kol SET full_name = name WHERE full_name IS NULL AND name IS NOT NULL"
        ))
        connection.execute(text(
            "UPDATE kol SET profile_url = channel_url "
            "WHERE profile_url IS NULL AND channel_url IS NOT NULL"
        ))
        connection.execute(text(
            "UPDATE kol SET followers = subscribers "
            "WHERE (followers IS NULL OR followers = 0) AND subscribers IS NOT NULL"
        ))
        connection.execute(text(
            "UPDATE kol SET content_category = niche "
            "WHERE content_category IS NULL AND niche IS NOT NULL"
        ))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_kol_snov_prospect_id ON kol (snov_prospect_id)"
        ))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_kol_snov_list_id ON kol (snov_list_id)"
        ))
        connection.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_kol_email_normalized "
            "ON kol (lower(trim(email))) "
            "WHERE email IS NOT NULL AND trim(email) <> ''"
        ))


def _ensure_mailbox_schema():
    """Apply the backwards-compatible mailbox migration on old databases."""
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "thread" not in table_names:
        return

    thread_columns = {column["name"] for column in inspector.get_columns("thread")}
    message_columns = (
        {column["name"] for column in inspector.get_columns("message")}
        if "message" in table_names
        else set()
    )
    with engine.begin() as connection:
        if "is_starred" not in thread_columns:
            connection.execute(text(
                "ALTER TABLE thread ADD COLUMN is_starred BOOLEAN NOT NULL DEFAULT false"
            ))
        if "message" in table_names and "attachments" not in message_columns:
            connection.execute(text(
                "ALTER TABLE message ADD COLUMN attachments JSON"
            ))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_thread_is_starred ON thread (is_starred)"
        ))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_message_direction_received "
            "ON message (direction, received_at)"
        ))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_message_thread_read "
            "ON message (thread_id, is_read)"
        ))
