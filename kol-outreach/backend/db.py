"""
KOL 外联中台 - 数据库连接与 Session 管理
SQLAlchemy 2.0 风格
"""
from sqlalchemy import create_engine
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
    """初始化数据库 - 建表(开发用,生产用 alembic 迁移)"""
    # 必须先 import 所有模型,确保它们注册到 Base.metadata
    import models  # noqa: F401  触发模型注册
    Base.metadata.create_all(bind=engine)
