"""
KOL 外联中台 - FastAPI 主入口
启动: uvicorn main:app --reload --host 0.0.0.0 --port 8000
"""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from db import init_db
from api import api_router
from services.snov_scheduler import start_snov_scheduler, stop_snov_scheduler

# 日志
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log_path = Path(settings.LOG_FILE)
log_path.parent.mkdir(parents=True, exist_ok=True)
file_handler = RotatingFileHandler(
    log_path,
    maxBytes=5 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
file_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
)
logging.getLogger().addHandler(file_handler)
# Legacy Snov endpoints require access_token in the query string. Never let
# httpx/httpcore print full external request URLs because they would expose it.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
# The OpenAI SDK logs complete prompt bodies at DEBUG level. Email content is
# sensitive and must never be written to application logs.
logging.getLogger("openai").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期:启动时建表"""
    logger.info(f"🚀 启动 {settings.APP_NAME} v{settings.APP_VERSION}")
    init_db()
    logger.info("✅ 数据库表已就绪")
    start_snov_scheduler()
    yield
    stop_snov_scheduler()
    logger.info("👋 关闭中")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
    description="KOL 外联中台 - AI 意向分析 + 运营协作",
)

# 跨域(前端开发服务器访问)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载路由
app.include_router(api_router, prefix="/api")


@app.get("/")
def root():
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "status": "running",
    }


@app.get("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )
