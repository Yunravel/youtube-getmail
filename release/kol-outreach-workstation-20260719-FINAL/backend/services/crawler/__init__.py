"""KOL 采集器服务。

把外部采集能力（关键词发现 → 多平台扩展 → 公开邮箱 → MX 校验 → 入库）
内嵌到后端服务层，供 API 端点与未来定时任务调用。

公开入口：:func:`pipeline.run_crawl`
业务规则：:mod:`config_rules`（关键词 / 国家白名单 / 种子等，新增产品改这里）
抓取抽象：:mod:`fetcher`（可插拔 Fetcher，首期 httpx）
"""
from services.crawler.pipeline import run_crawl

__all__ = ["run_crawl"]
