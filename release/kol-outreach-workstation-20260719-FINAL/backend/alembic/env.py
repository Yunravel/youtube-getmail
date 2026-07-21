"""Alembic 运行环境配置。

DATABASE_URL 复用主应用的 `config.settings`，不在此重复硬编码，
保证开发 SQLite / 生产 PostgreSQL 的切换与 uvicorn 进程一致。
"""
import sys
from logging.config import fileConfig
from pathlib import Path

# alembic 把 env.py 当独立模块加载，不会自动把 backend/ 加入 sys.path。
# 这里显式插入 env.py 的父目录（即 backend/），使 `config` / `models` / `db` 可被 import。
BACKEND_DIR = str(Path(__file__).resolve().parent.parent)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from alembic import context
from sqlalchemy import engine_from_config, pool

from config import settings
import models  # noqa: F401  触发所有模型注册到 Base.metadata
from db import Base

config = context.config

# 日志配置（alembic.ini [loggers] 段）
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 把主应用的 DATABASE_URL 注入 alembic
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：生成 SQL 脚本不连库。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接数据库执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
