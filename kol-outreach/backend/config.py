"""
KOL 外联中台 - 配置模块
所有配置从环境变量读取,生产环境用 .env 文件覆盖默认值
"""
import os
from pathlib import Path
from typing import Optional

try:
    # python-dotenv 可选,开发时用 .env 文件
    from dotenv import load_dotenv
    # Local development commonly keeps production-like settings at the
    # repository root. Load it first so backend/.env only fills missing keys.
    load_dotenv(Path(__file__).resolve().parent.parent / ".env.prod")
    load_dotenv()
except ImportError:
    pass


class Settings:
    """应用配置 - 通过环境变量注入"""

    # ===== 应用 =====
    APP_NAME: str = "KOL Outreach Platform"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = os.getenv("DEBUG", "true").lower() == "true"
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    LOG_FILE: str = os.getenv("LOG_FILE", "./logs/kol-outreach.log")

    # ===== 数据库 =====
    # 开发用 SQLite,生产用 PostgreSQL
    # SQLite:    sqlite:///./kol.db
    # PostgreSQL: postgresql+psycopg2://user:pass@localhost:5432/kol
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "sqlite:///./kol_outreach.db"
    )

    # ===== 跨域 =====
    # 前端开发服务器地址
    CORS_ORIGINS: str = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173"
    )

    # ===== OpenAI =====
    OPENAI_API_KEY: Optional[str] = os.getenv("OPENAI_API_KEY")
    # OpenAI-compatible providers (for example DeepSeek) can override the API root.
    OPENAI_BASE_URL: Optional[str] = os.getenv("OPENAI_BASE_URL") or None
    # 意向分析用 mini 省钱,开场白用 4o 质量高
    OPENAI_MODEL_INTENT: str = os.getenv("OPENAI_MODEL_INTENT", "gpt-4o-mini")
    OPENAI_MODEL_PERSONALIZE: str = os.getenv("OPENAI_MODEL_PERSONALIZE", "gpt-4o")

    # ===== Snov webhook =====
    # Snov 会把 campaign_email / campaign_reply 事件推送到中台。
    # 生产环境必须设置强随机值；缺失或默认值会拒绝 webhook。
    SNOV_WEBHOOK_TOKEN: str = os.getenv(
        "SNOV_WEBHOOK_TOKEN",
        os.getenv("WEBHOOK_TOKEN", "")
    )
    # 仅用于管理 Snov webhook、读取 Campaign 和补拉历史数据；回信接收本身不依赖它。
    SNOV_CLIENT_ID: Optional[str] = os.getenv("SNOV_CLIENT_ID")
    SNOV_CLIENT_SECRET: Optional[str] = os.getenv("SNOV_CLIENT_SECRET")
    SNOV_API_BASE: str = os.getenv("SNOV_API_BASE", "https://api.snov.io")
    SNOV_SYNC_ENABLED: bool = os.getenv("SNOV_SYNC_ENABLED", "true").lower() == "true"
    # Polling complements webhooks and repairs missed events. Snov itself may
    # detect mailbox replies less frequently than this polling interval.
    SNOV_SYNC_INTERVAL_SECONDS: int = max(
        60, int(os.getenv("SNOV_SYNC_INTERVAL_SECONDS", "120"))
    )

    # ===== 发信约束(防封安全阀) =====
    MAX_DAILY_SEND_PER_MAILBOX: int = int(os.getenv("MAX_DAILY_SEND_PER_MAILBOX", "30"))
    WARMUP_MIN_DAYS: int = int(os.getenv("WARMUP_MIN_DAYS", "14"))

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def snov_webhook_is_configured(self) -> bool:
        """避免用示例 token 或空 token 暴露 webhook。"""
        unsafe_values = {
            "",
            "change-me-in-production",
            "change-me-to-a-random-string",
            "change-me-to-a-long-random-string",
            "change-me-to-random",
        }
        return (
            self.SNOV_WEBHOOK_TOKEN not in unsafe_values
            and not self.SNOV_WEBHOOK_TOKEN.startswith("replace-with-")
        )

settings = Settings()
