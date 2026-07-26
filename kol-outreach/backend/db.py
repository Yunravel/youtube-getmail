"""
KOL 外联中台 - 数据库连接与 Session 管理
SQLAlchemy 2.0 风格
"""
import logging
import sqlite3

from sqlalchemy import String, create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from config import settings

logger = logging.getLogger(__name__)

# SQLite 需要开 check_same_thread=False 才能在 FastAPI 多线程用
_is_sqlite_url = settings.DATABASE_URL.startswith("sqlite")
connect_args = {}
if _is_sqlite_url:
    connect_args = {"check_same_thread": False}

# 连接池参数仅对非 SQLite（PostgreSQL 等生产级库）生效。SQLite 走默认
# SingletonThreadPool/NullPool，传 pool_size 等会引发警告或语义不符。
_engine_kwargs: dict = {
    "echo": False,           # 调试时可开 True 打印 SQL
    "pool_pre_ping": True,   # 借出前 ping，避免用上被 PG 端/防火墙断开的死连接
}
if not _is_sqlite_url:
    _engine_kwargs.update(
        pool_size=10,        # 每进程常驻连接数（PG max_connections=100，留余量）
        max_overflow=20,     # 突发可借额外连接（单容器上限 30）
        pool_recycle=1800,   # 30min 回收，防长连接被服务端静默断开后僵死
        pool_timeout=30,     # 池耗尽时等连接最多 30s，避免无限挂起
    )

engine = create_engine(settings.DATABASE_URL, connect_args=connect_args, **_engine_kwargs)


# ---- SQLite SAVEPOINT 语义修复(SQLAlchemy 官方文档方案) ----
# pysqlite 默认只在 DML 前隐式 BEGIN、SELECT 不开事务。若事务里还没发生过
# 写入就执行 begin_nested(),SAVEPOINT 会成为最外层事务,而 SQLite 规定
# 最外层 SAVEPOINT 的 RELEASE 等价于 COMMIT——本该由调用方决定的提交被
# 提前发生,rollback 也撤不掉。业务里并发去重(feishu_push.enqueue_message_sync、
# auto_reply._upsert_task)依赖 SAVEPOINT 吸收唯一约束冲突,必须保证其语义正确。
# 修法:禁用 pysqlite 的隐式 BEGIN,由 SQLAlchemy 的 begin 事件显式发 BEGIN。
# 注册在 Engine 类上,应用引擎与测试各自建的 SQLite 引擎全部覆盖;PG 连接
# 不受影响(isinstance/方言名双重守卫)。


@event.listens_for(Engine, "connect")
def _sqlite_disable_implicit_begin(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        dbapi_connection.isolation_level = None


@event.listens_for(Engine, "begin")
def _sqlite_explicit_begin(conn):
    if conn.dialect.name != "sqlite":
        return
    # 测试用 StaticPool 时多个 session 共享同一条物理连接,别人已开事务
    # 就不能再 BEGIN(sqlite 会报 cannot start a transaction within a
    # transaction),此时沿用该事务即可——与修复前的共享语义一致。
    dbapi_connection = conn.connection.dbapi_connection
    if isinstance(dbapi_connection, sqlite3.Connection) and dbapi_connection.in_transaction:
        return
    conn.exec_driver_sql("BEGIN")


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 所有 ORM 模型的基类
Base = declarative_base()


@event.listens_for(SessionLocal, "before_flush")
def _truncate_oversized_strings(session, flush_context, instances):
    """flush 前把超过 VARCHAR(n) 限长的字符串统一截断 —— ORM 层单点防线。

    为什么放这里而不是各写入点:
    - SQLite(本地开发库)不检查 VARCHAR 长度,超长值静默入库,开发时完全无感;
      生产 PostgreSQL 会抛 StringDataRightTruncation → 接口 500。典型触发场景:
      Snov webhook 送来超长邮件主题,500 后被 Snov 反复重投,形成持续报错。
    - 入库路径很多(get_db 请求、webhook、IMAP ingest、CSV 导入、脚本),都经
      db.SessionLocal 建 session,注册在 sessionmaker 工厂上的监听器全路径覆盖
      (``SessionLocal(bind=其他引擎)`` 创建的 session 同样触发,测试正是靠这一点)。
      在每个写入点各自截断既容易漏,也没必要。

    截断只影响病态输入(正常业务值远短于限长)。message_id 这类去重键被截断
    后理论上存在碰撞风险,但可接受——不截断的替代结局是整个请求 500,数据同样
    丢失且伴随重投风暴。截断必须留观测痕迹(logger.warning),静默丢数据不可接受。

    只处理映射为普通列属性的字符串列:mapper.column_attrs 天然排除 relationship/
    synonym 等花活;Text 是 String 子类但 length 为 None,不会命中;JSON 列不受影响。
    """
    for obj in session.new | session.dirty:
        state = inspect(obj)
        mapper = state.mapper
        for attr in mapper.column_attrs:
            column = attr.columns[0]
            if not isinstance(column.type, String) or not column.type.length:
                continue
            # 只看已加载/已赋值的属性(state.dict),避免触碰 deferred/expired
            # 属性时触发额外的惰性加载——没加载过的属性也不可能带着超长新值。
            value = state.dict.get(attr.key)
            if not isinstance(value, str) or len(value) <= column.type.length:
                continue
            setattr(obj, attr.key, value[: column.type.length])
            logger.warning(
                "入库字符串超长,已截断: %s.%s 长度 %d → 限长 %d",
                column.table.name, column.name, len(value), column.type.length,
            )


def get_db():
    """FastAPI 依赖注入:每个请求拿一个独立 session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _is_sqlite() -> bool:
    """当前 DATABASE_URL 是否指向 SQLite（本地开发库）。"""
    return _is_sqlite_url


def init_db():
    """初始化数据库 - 建表(开发用,生产用 alembic 迁移)。

    历史背景：本项目长期用 ``create_all`` + 启动时 ``ALTER TABLE`` 维护 schema，
    生产库靠 ``_ensure_*`` 函数补列。自引入 alembic（2026-07）后，生产 schema 变更
    应走 ``alembic upgrade head``；此处仅保留给本地 SQLite 开发快速建表，避免回归。
    新增列/表请写到 alembic migration，不要在这里继续堆 ``_ensure_*``。

    生产守卫（DATABASE_DEVELOPMENT.md §10）：非 SQLite（PostgreSQL 等生产级库）时，
    跳过 ``create_all`` 与 ``_ensure_*``，schema 责任完全交给 alembic。
    部署时必须由 Dockerfile/compose 在启动应用前执行 ``alembic upgrade head``。
    """
    if not _is_sqlite():
        logger.warning(
            "init_db: 检测到非 SQLite 数据库(%s)，跳过 create_all/_ensure_*。"
            "生产 schema 由 alembic 管理，部署前请确认已执行 'alembic upgrade head'。",
            settings.DATABASE_URL.split("://", 1)[0] if "://" in settings.DATABASE_URL
            else settings.DATABASE_URL,
        )
        return

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
    credential_columns = (
        {column["name"] for column in inspector.get_columns("mailbox_credential")}
        if "mailbox_credential" in table_names
        else set()
    )
    scheduled_reply_columns = (
        {column["name"] for column in inspector.get_columns("scheduled_reply")}
        if "scheduled_reply" in table_names
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
        if "message" in table_names and "rfc_message_id" not in message_columns:
            connection.execute(text("ALTER TABLE message ADD COLUMN rfc_message_id VARCHAR(500)"))
        if "message" in table_names and "received_at_estimated" not in message_columns:
            connection.execute(text(
                "ALTER TABLE message ADD COLUMN received_at_estimated BOOLEAN NOT NULL DEFAULT false"
            ))
        if "mailbox_credential" in table_names:
            credential_additions = {
                "smtp_host": "VARCHAR(100) NOT NULL DEFAULT 'smtp.gmail.com'",
                "smtp_port": "INTEGER NOT NULL DEFAULT 465",
                "smtp_use_ssl": "BOOLEAN NOT NULL DEFAULT true",
                "smtp_verified_at": "TIMESTAMP",
                "smtp_last_error": "TEXT",
            }
            for name, sql_type in credential_additions.items():
                if name not in credential_columns:
                    connection.execute(text(
                        f"ALTER TABLE mailbox_credential ADD COLUMN {name} {sql_type}"
                    ))
        if "scheduled_reply" in table_names and "manual_override" not in scheduled_reply_columns:
            connection.execute(text(
                "ALTER TABLE scheduled_reply ADD COLUMN manual_override BOOLEAN NOT NULL DEFAULT false"
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
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_message_rfc_message_id ON message (rfc_message_id)"
        ))
