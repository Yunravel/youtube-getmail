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
    PORT: int = int(os.getenv("PORT") or "8000")
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
        60, int(os.getenv("SNOV_SYNC_INTERVAL_SECONDS") or "120")
    )

    # ===== IMAP 附件同步（直连 Gmail 抓真附件，绕开 Snov）=====
    # Snov webhook/API 不传附件数据；KOL 的真附件（PDF/图片）只在发信邮箱的
    # Gmail 收件箱里。这里直连 Gmail IMAP 抓回来，存本地磁盘。
    # 定时轮询（每 10 分钟）查未读邮件；前端手动触发可指定时间范围。
    IMAP_SYNC_ENABLED: bool = os.getenv("IMAP_SYNC_ENABLED", "true").lower() == "true"
    IMAP_SYNC_INTERVAL_SECONDS: int = max(
        60, int(os.getenv("IMAP_SYNC_INTERVAL_SECONDS") or "600")
    )
    # SOCKS5 代理（Clash 等）。国内直连 imap.gmail.com 会被墙，必须走代理。
    # 127.0.0.1:7897 是 Clash Verge 默认 mixed 端口。
    IMAP_PROXY_ENABLED: bool = os.getenv("IMAP_PROXY_ENABLED", "true").lower() == "true"
    IMAP_PROXY_HOST: str = os.getenv("IMAP_PROXY_HOST", "127.0.0.1")
    IMAP_PROXY_PORT: int = int(os.getenv("IMAP_PROXY_PORT") or "7897")
    # 附件本地存储目录（相对 backend/ 工作目录）
    ATTACHMENT_STORAGE_DIR: str = os.getenv("ATTACHMENT_STORAGE_DIR", "./data/attachments")
    # 邮箱应用专用密码的 Fernet 主密钥（base64 urlsafe，32 字节）。
    # 缺失时降级为明文存储并打警告（仅适合 dev）。生产必须配。
    # 生成方法：python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    ATTACHMENT_MASTER_KEY: str = os.getenv("ATTACHMENT_MASTER_KEY", "")

    # ===== 发信约束(防封安全阀) =====
    MAX_DAILY_SEND_PER_MAILBOX: int = int(os.getenv("MAX_DAILY_SEND_PER_MAILBOX") or "30")
    WARMUP_MIN_DAYS: int = int(os.getenv("WARMUP_MIN_DAYS") or "14")

    # ===== 采集器（KOL 爬虫，内嵌于 services/crawler/）=====
    # 关键词发现 / 频道 about 抓取 / MX 校验三阶段的并发上限
    CRAWLER_MAX_CONCURRENCY_DISCOVERY: int = int(os.getenv("CRAWLER_MAX_CONCURRENCY_DISCOVERY") or "3")
    CRAWLER_MAX_CONCURRENCY_CHANNEL: int = int(os.getenv("CRAWLER_MAX_CONCURRENCY_CHANNEL") or "6")
    CRAWLER_MAX_CONCURRENCY_MX: int = int(os.getenv("CRAWLER_MAX_CONCURRENCY_MX") or "30")
    # 单页抓取超时（秒）与请求间隔（秒，礼貌限速）
    CRAWLER_REQUEST_TIMEOUT: int = int(os.getenv("CRAWLER_REQUEST_TIMEOUT") or "20")
    CRAWLER_REQUEST_INTERVAL: float = float(os.getenv("CRAWLER_REQUEST_INTERVAL") or "0.2")
    # User-Agent（与外部邮件平台抓取保持一致风格）
    CRAWLER_USER_AGENT: str = os.getenv(
        "CRAWLER_USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/138 Safari/537.36",
    )
    # 采集端点鉴权 token（触发采集是带副作用的写入操作，不能公开）。
    # 前端请求需带 X-Crawler-Token 头；空值或占位符会被判定为未配置。
    CRAWLER_TOKEN: str = os.getenv("CRAWLER_TOKEN", "")
    # 住宅代理池。逗号分隔，每项形如 http://user:pass@host:port 或 socks5://host:port。
    # 数据中心 IP 几乎必被 YouTube 封；生产环境必须配住宅代理。
    # 留空则直连（仅适合本地开发或住宅网络直接出口）。
    CRAWLER_PROXIES: str = os.getenv("CRAWLER_PROXIES", "")
    # 代理轮换策略：round_robin（每次请求轮流用下一个）。
    CRAWLER_PROXY_STRATEGY: str = os.getenv("CRAWLER_PROXY_STRATEGY", "round_robin")
    # 定时采集：留空或 false 关闭；填 cron 表达式启用（如 "0 2 * * *" = 每天凌晨2点）
    # 建议低频（每天或每周），避免被 YouTube 限流。需配合住宅代理池。
    CRAWLER_SCHEDULE_CRON: str = os.getenv("CRAWLER_SCHEDULE_CRON", "")
    # 定时采集跑哪些产品（逗号分隔，如 "Dreamina,Pippit"）
    CRAWLER_SCHEDULE_PRODUCTS: str = os.getenv("CRAWLER_SCHEDULE_PRODUCTS", "Dreamina,Pippit,Kimi,Dola")

    # ===== 采集功能开关（每个独立，默认值可被请求参数覆盖）=====
    # 多平台扩展：从 YouTube about 抽 IG/TikTok/X 外链生成多平台候选行
    CRAWLER_ENABLE_ENRICH: bool = os.getenv("CRAWLER_ENABLE_ENRICH", "true").lower() == "true"
    # 公开邮箱采集 + MX 校验：从 YouTube about 抽邮箱并做 DNS MX 校验
    CRAWLER_ENABLE_EMAIL: bool = os.getenv("CRAWLER_ENABLE_EMAIL", "true").lower() == "true"
    # 官网深度邮箱采集：跟进 about 里的官网链接，用浏览器渲染抓 /contact 页邮箱。
    # 需要 playwright + chromium（pip install playwright && playwright install chromium）。
    # 默认关：耗时显著增加（每站点起浏览器渲染），且对代理质量要求高。
    CRAWLER_ENABLE_DEEP_EMAIL: bool = os.getenv("CRAWLER_ENABLE_DEEP_EMAIL", "false").lower() == "true"
    # 抓取后端：httpx（默认，快但抓不到 JS 渲染站点）/ playwright（慢但能渲染）
    # 深度邮箱采集强制用 playwright；这里控制 about 页与官网用哪种。
    CRAWLER_FETCHER: str = os.getenv("CRAWLER_FETCHER", "httpx")

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def crawler_token_is_configured(self) -> bool:
        """采集端点是否启用了 token 鉴权。

        未配置时端点直接拒绝（触发采集是带副作用的写入，宁可关闭也不公开）。
        """
        unsafe = {"", "change-me", "change-me-in-production", "replace-with-a-strong-token"}
        return (
            self.CRAWLER_TOKEN not in unsafe
            and not self.CRAWLER_TOKEN.startswith("replace-with-")
        )

    @property
    def crawler_proxies_list(self) -> list[str]:
        """解析 CRAWLER_PROXIES 为代理 URL 列表（去空白与空项）。"""
        return [p.strip() for p in self.CRAWLER_PROXIES.split(",") if p.strip()]

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

    @property
    def attachment_master_key_configured(self) -> bool:
        """ATTACHMENT_MASTER_KEY 是否已配置为合法的 Fernet 密钥。"""
        return bool(self.ATTACHMENT_MASTER_KEY) and self.ATTACHMENT_MASTER_KEY != "change-me"

    @property
    def attachment_storage_path(self) -> Path:
        """附件存储目录的绝对 Path（自动创建）。"""
        p = Path(self.ATTACHMENT_STORAGE_DIR).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

settings = Settings()
