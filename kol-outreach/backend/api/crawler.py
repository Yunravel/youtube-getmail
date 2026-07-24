"""采集器接口 —— 触发 KOL 采集后台任务 + 查询进度。

完全复刻 ``api/threads.py`` 的 AI 回填任务模式：
- 内存态任务表 ``_crawl_jobs``（重启丢失可接受，任务幂等可重跑）
- 单并发（同时只允许一个采集任务，冲突 409）
- ``BackgroundTasks`` 调度，立即返回 job_id
- 前端轮询 ``/crawler/status`` 看进度

鉴权：所有端点强制 ``X-Crawler-Token`` 头（与 webhook token 同模式，
``hmac.compare_digest``）。触发采集是带副作用的写入（真实抓取 + DNS + 写库），
绝不能像只读看板那样公开。
"""
import hmac
import threading
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import settings
from db import get_db
from models import CrawlerProduct
from services.crawler.config_rules import productTerms

router = APIRouter()

# ===== 采集后台任务的内存状态（重启丢失可接受，任务幂等可重跑）=====
# 同时只允许一个采集任务在跑，避免并发抓取叠加以致被限速/封禁。
_crawl_jobs: dict[str, dict] = {}

# 本地开发放行：这些 Host 视为可信同源，跳过 token 校验。
# 生产部署在公网域名上，Host 不会命中这里，仍强制 token。
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def require_crawler_token(
    request: Request,
    x_crawler_token: str = Header(default=""),
) -> None:
    """采集端点鉴权依赖。比对 X-Crawler-Token 头与配置的 CRAWLER_TOKEN。

    - 本地开发放行：请求 Host 为 localhost/127.0.0.1/::1 时跳过校验，
      免配 token 即可使用（vite dev 代理、本机直连都符合）
    - 生产（公网域名）：
        - 未配置 token（空/占位符）→ 503（宁可关闭也不公开带副作用的端点）
        - 不匹配 → 401
    用恒定时间比较防时序侧信道，与 webhook token 校验一致。

    安全性说明：
      只看 Host 头（request.url.hostname），不看 client.host（TCP 对端 IP）。
      因为生产部署走反向代理（Caddy/Traefik），后端看到的 client.host 永远是
      代理的内网 IP（127.0.0.1 或 172.x），用它判断会把生产误判为"本地"放行。
      反向代理默认保留原始 Host 头（公网域名），所以 url.hostname 能正确区分。
    """
    host = (request.url.hostname or "").lower()
    if host in _LOCAL_HOSTS:
        return  # 本地开发放行

    if not settings.crawler_token_is_configured:
        raise HTTPException(503, "Crawler token not configured; endpoint disabled")
    if not x_crawler_token or not hmac.compare_digest(x_crawler_token, settings.CRAWLER_TOKEN):
        raise HTTPException(401, "Invalid crawler token")


class CrawlIn(BaseModel):
    """采集请求体。

    两种模式二选一：
    - 产品模式：选 products（用词表展开查询），products 不能为空
    - 自定义模式：products 留空 + 传 custom_queries，用自定义关键词跑
      （不限于词表，可随时补充新词扩大覆盖）
    """
    products: list[str] = []         # 内置产品或数据库中的自定义产品
    custom_queries: list[str] = []   # 自定义查询词（与产品词表正交，可自由扩展）
    enable_enrich: bool = True       # 多平台外链扩展（IG/TikTok/X）
    enable_email: bool = True        # 公开邮箱采集 + MX 校验（YouTube about）
    enable_deep_email: bool = False  # 官网深度邮箱采集（需 Playwright + 代理，慢，默认关）
    enable_scrape_creators: bool = False  # ScrapeCreators API 抓取（按 API 计费，默认关）

    @field_validator("products")
    @classmethod
    def _validate_products(cls, v: list[str]) -> list[str]:
        seen: list[str] = []
        for p in v:
            p = (p or "").strip()
            if not p or len(p) > 50:
                continue
            if p not in seen:
                seen.append(p)
        return seen

    @field_validator("custom_queries")
    @classmethod
    def _validate_custom_queries(cls, v: list[str]) -> list[str]:
        # 清洗：去空白、去空、去重保序、长度限制
        seen: list[str] = []
        for q in v:
            q = (q or "").strip()
            if q and len(q) <= 200 and q not in seen:
                seen.append(q)
        return seen[:500]  # 单次最多 500 个自定义词，防止滥用

    @model_validator(mode="after")
    def _at_least_one_source(self):
        if not self.products and not self.custom_queries:
            raise ValueError("至少选一个产品，或提供自定义关键词")
        return self


def _run_crawl_job(
    job_id: str,
    products: list[str],
    enable_enrich: bool,
    enable_email: bool,
    custom_queries: list[str] | None = None,
    enable_deep_email: bool = False,
    custom_product_terms: dict[str, list[str]] | None = None,
    enable_scrape_creators: bool = False,
) -> None:
    """后台任务函数：被 BackgroundTasks 调度，更新内存 job 状态。

    独立 session（后台任务跨请求生命周期，不能复用请求 session）。
    """
    job = _crawl_jobs[job_id]
    job["phase"] = "discovery"
    job["processed"] = 0
    job["total"] = 0

    def on_progress(done: int, total: int, phase: str) -> None:
        job["processed"] = done
        job["total"] = total
        job["phase"] = phase

    # 采集 I/O 期间不持有 DB session（DATABASE_DEVELOPMENT.md §8）；
    # run_crawl 内部在抓取完成后用独立短事务入库，故此处不传 db。
    try:
        from services.crawler import run_crawl
        result = run_crawl(
            products,
            enable_enrich=enable_enrich,
            enable_email=enable_email,
            enable_deep_email=enable_deep_email,
            enable_scrapecreators=enable_scrape_creators,
            on_progress=on_progress,
            commit=True,
            batch=job["batch"],
            custom_queries=custom_queries or [],
            custom_product_terms=custom_product_terms or {},
        )
        job["status"] = "done"
        job["result"] = result
        job["finished_at"] = datetime.utcnow().isoformat()
    except Exception as e:
        job["status"] = "failed"
        job["error"] = str(e)
        job["finished_at"] = datetime.utcnow().isoformat()


class CrawlerProductIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    keywords: list[str] = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("产品名称不能为空")
        return value

    @field_validator("keywords")
    @classmethod
    def _clean_keywords(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            value = (value or "").strip()
            if value and len(value) <= 200 and value not in result:
                result.append(value)
        if not result:
            raise ValueError("至少需要一个有效关键词")
        return result


def _product_out(item: CrawlerProduct) -> dict:
    return {
        "id": item.id,
        "product": item.name,
        "keyword_count": len(item.keywords or []),
        "keywords": item.keywords or [],
        "built_in": False,
    }


def _ensure_name_available(db: Session, name: str, exclude_id: int | None = None) -> None:
    normalized = name.casefold()
    if normalized in {value.casefold() for value in productTerms}:
        raise HTTPException(409, "产品名称与内置产品重复")
    query = db.query(CrawlerProduct).filter(CrawlerProduct.name_normalized == normalized)
    if exclude_id is not None:
        query = query.filter(CrawlerProduct.id != exclude_id)
    if query.first():
        raise HTTPException(409, "自定义产品名称已存在")


@router.get("/products")
def list_products(
    _=Depends(require_crawler_token),
    db: Session = Depends(get_db),
):
    """返回可选产品列表 + 关键词数（供前端渲染复选框）。"""
    built_in = [
        {"id": None, "product": p, "keyword_count": len(kws), "keywords": None, "built_in": True}
        for p, kws in productTerms.items()
    ]
    custom = db.query(CrawlerProduct).order_by(CrawlerProduct.created_at, CrawlerProduct.id).all()
    return built_in + [_product_out(item) for item in custom]


@router.post("/products")
def create_product(
    body: CrawlerProductIn,
    _=Depends(require_crawler_token),
    db: Session = Depends(get_db),
):
    _ensure_name_available(db, body.name)
    item = CrawlerProduct(
        name=body.name,
        name_normalized=body.name.casefold(),
        keywords=body.keywords,
    )
    db.add(item)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "自定义产品名称已存在")
    db.refresh(item)
    return _product_out(item)


@router.put("/products/{product_id}")
def update_product(
    product_id: int,
    body: CrawlerProductIn,
    _=Depends(require_crawler_token),
    db: Session = Depends(get_db),
):
    item = db.get(CrawlerProduct, product_id)
    if not item:
        raise HTTPException(404, "自定义产品不存在")
    _ensure_name_available(db, body.name, exclude_id=product_id)
    item.name = body.name
    item.name_normalized = body.name.casefold()
    item.keywords = body.keywords
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "自定义产品名称已存在")
    db.refresh(item)
    return _product_out(item)


@router.delete("/products/{product_id}")
def delete_product(
    product_id: int,
    _=Depends(require_crawler_token),
    db: Session = Depends(get_db),
):
    item = db.get(CrawlerProduct, product_id)
    if not item:
        raise HTTPException(404, "自定义产品不存在")
    db.delete(item)
    db.commit()
    return {"deleted": product_id}


@router.post("")
def start_crawl(
    body: CrawlIn,
    background_tasks: BackgroundTasks,
    _=Depends(require_crawler_token),
    db: Session = Depends(get_db),
):
    """触发采集后台任务。立即返回 job_id，前端轮询 /crawler/status 看进度。

    同时只允许一个采集任务在跑（避免并发抓取叠加被限速）。
    """
    running = [j for j in _crawl_jobs.values() if j["status"] == "running"]
    if running:
        raise HTTPException(409, f"已有采集任务在跑: {running[0]['job_id']}")

    custom_rows = db.query(CrawlerProduct).all() if body.products else []
    custom_by_normalized = {item.name_normalized: item for item in custom_rows}
    canonical_products: list[str] = []
    custom_product_terms: dict[str, list[str]] = {}
    unknown: list[str] = []
    built_in_by_normalized = {name.casefold(): name for name in productTerms}
    for requested in body.products:
        normalized = requested.casefold()
        if normalized in built_in_by_normalized:
            canonical_products.append(built_in_by_normalized[normalized])
        elif normalized in custom_by_normalized:
            item = custom_by_normalized[normalized]
            canonical_products.append(item.name)
            custom_product_terms[item.name] = item.keywords or []
        else:
            unknown.append(requested)
    if unknown:
        raise HTTPException(422, f"未知产品: {unknown}，请刷新产品列表后重试")

    job_id = str(uuid.uuid4())[:8]
    _crawl_jobs[job_id] = {
        "job_id": job_id,
        "status": "running",
        "phase": "queued",
        "processed": 0,
        "total": 0,
        "products": canonical_products,
        "custom_queries": body.custom_queries,
        "enable_enrich": body.enable_enrich,
        "enable_email": body.enable_email,
        "enable_deep_email": body.enable_deep_email,
        "enable_scrape_creators": body.enable_scrape_creators,
        "batch": f"crawl-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
        "started_at": datetime.utcnow().isoformat(),
    }
    background_tasks.add_task(
        _run_crawl_job, job_id, canonical_products, body.enable_enrich,
        body.enable_email, body.custom_queries, body.enable_deep_email,
        custom_product_terms, body.enable_scrape_creators,
    )
    return {"job_id": job_id, "batch": _crawl_jobs[job_id]["batch"]}


@router.get("/status")
def crawl_status(_=Depends(require_crawler_token)):
    """查最新采集任务进度。返回最近一个 job 的状态（复刻 /threads/backfill-status）。"""
    if not _crawl_jobs:
        return {"job_id": None, "status": "idle"}
    return list(_crawl_jobs.values())[-1]
