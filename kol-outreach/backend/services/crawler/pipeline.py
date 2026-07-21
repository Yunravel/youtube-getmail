"""采集编排：发现 → 多平台扩展 → 公开邮箱 → MX 校验 → 入库。

这是采集器的总入口 :func:`run_crawl`，CLI 与 HTTP 端点共用（复刻
``run_import`` / ``run_backfill`` 的"单一入口、commit 开关、进度回调"模式）。

阶段流水线（每阶段都可开关）：
  1. 发现   —— 对每个产品×关键词，平台搜索 → 频道 handle
  2. 扩展   —— 对每个频道抓 about → 抽 IG/TikTok/X 外链（多平台候选）
  3. 邮箱   —— 对每个频道 about 抽公开邮箱 + 商务信号识别
  4. MX     —— 对邮箱域做 MX 校验，过滤无效邮箱
  5. 入库   —— 调 upsert_candidates 双写 candidate + 晋升 kol/kol_email

并发：每阶段独立的并发上限（discovery/channel/mx），由配置控制。
幂等：(platform, account) 与邮箱去重在 upsert_candidates 内完成，重跑安全。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable, Optional
from urllib.parse import quote

from sqlalchemy.orm import Session

from config import settings
from services.crawler import enrich, normalize, youtube
from services.crawler.config_rules import (
    allowedByProduct,
    excludedHandles,
    make_queries,
    positiveSeeds,
)
from services.crawler.fetcher import HttpxFetcher, gather_pool

logger = logging.getLogger(__name__)

# 进度回调签名：(done, total, phase) -> None
ProgressFn = Callable[[int, int, str], None]

# 深度邮箱采集的 Playwright Fetcher 全局实例（任务内共享，任务结束关闭）
_deep_email_fetcher = None


async def _discover(
    fetcher,
    products: list[str],
    on_progress: ProgressFn | None,
    custom_queries: list[str] | None = None,
    custom_product_terms: dict[str, list[str]] | None = None,
) -> list[dict]:
    """阶段 1：平台搜索发现频道 handle。

    查询来源：
    - products 展开词表查询（每词 1-3 变体）
    - custom_queries 直接作为查询词（"自定义" 产品标记）
    两者合并去重后跑。返回 ``[{"handle", "products", "queries"}, ...]``。
    """
    queries = make_queries(products, custom_product_terms)
    # 合并自定义关键词（标记为 "自定义" 产品，便于后续识别来源）
    for q in (custom_queries or []):
        queries.append(("自定义", q))

    # handle(小写) -> {handle, products, queries}
    found: dict[str, dict] = {}
    done = 0

    async def _search(item: tuple[str, str]) -> None:
        nonlocal done
        product, query = item
        url = f"https://www.youtube.com/results?search_query={quote(query)}"
        html = await fetcher.fetch_text(url)
        for handle in youtube.handles_from_search(html):
            key = handle.lower()
            if key in excludedHandles:
                continue
            if key not in found:
                found[key] = {"handle": handle, "products": set(), "queries": set()}
            found[key]["products"].add(product)
            found[key]["queries"].add(query)
        done += 1
        if on_progress and (done % 5 == 0 or done == len(queries)):
            on_progress(done, len(queries), "discovery")

    concurrency = getattr(settings, "CRAWLER_MAX_CONCURRENCY_DISCOVERY", 3)
    await gather_pool(queries, concurrency, _search)
    if on_progress:
        on_progress(len(queries), len(queries), "discovery")
    return list(found.values())


async def _enrich_and_collect_emails(
    fetcher,
    candidates: list[dict],
    *,
    enable_enrich: bool,
    enable_email: bool,
    enable_deep_email: bool,
    on_progress: ProgressFn | None,
) -> list[dict]:
    """阶段 2+3：对每个频道抓 about，做多平台扩展（可选）与公开邮箱采集（可选）。

    返回多平台候选行 dict 列表（字段名对齐 kol_candidate，但 products/queries
    等中间字段保留为 list，最后由 _finalize_rows 清洗）。
    每个候选可能含 contact_email（邮箱采集开启且有邮箱时）。
    """
    total = len(candidates)
    done = 0
    # 用 (platform, account_lower) 去重，跨频道重复只保留首个
    seen: set[tuple[str, str]] = set()
    out_rows: list[dict] = []

    def _emit(row: dict) -> None:
        key = (row["platform"], row["account"].lower())
        if key in seen:
            return
        seen.add(key)
        out_rows.append(row)

    async def _process(candidate: dict) -> None:
        nonlocal done
        handle = candidate["handle"]
        about_url = f"https://www.youtube.com{handle}/about"
        html = await fetcher.fetch_text(about_url)

        # Bug 修复：about 抓不到（404/网络/反爬）时直接跳过，不生成空行污染数据库。
        # 空白 HTML 或过短（YouTube 真实 about 页至少几百 KB）视为失败。
        if not html or len(html) < 5000:
            logger.debug("skip empty about %s (len=%d)", about_url, len(html))
            done += 1
            if on_progress and (done % 5 == 0 or done == total):
                on_progress(done, total, "enrich")
            return

        # 解析频道元信息（即使没邮箱也要做，用于多平台扩展的国家/画像）
        title = youtube.meta_content(html, "title").replace(" - YouTube", "").strip()
        description = youtube.full_channel_description(html)
        subscriber_text = youtube.field_from_html(html, "subscriberCountText")
        followers = normalize.parse_subscriber_count(subscriber_text)

        # 邮箱采集（可选）
        emails_info: list[dict] = []
        if enable_email:
            # 优先：从 YouTube about 页简介直接抽邮箱
            for email in youtube.extract_emails(description):
                is_business = youtube.business_signal(description, email)
                emails_info.append({
                    "email": email,
                    "is_business": is_business,
                    "source_url": about_url,
                })
            # 兜底：about 没邮箱时，从 about 里的官网链接走深度采集。
            # 用 Playwright Fetcher 渲染（httpx 抓不到 JS 站点 / 被 CDN 拦），
            # 受 CRAWLER_ENABLE_DEEP_EMAIL 开关控制。
            # 深度采集显著拖慢任务（每站点起浏览器渲染），且依赖住宅代理质量，故默认关。
            if not emails_info and enable_deep_email:
                from services.crawler import site_email
                # PlaywrightFetcher 懒加载共享：整个任务用一个浏览器实例，避免每站点重启
                global _deep_email_fetcher
                if _deep_email_fetcher is None:
                    from services.crawler.fetcher import PlaywrightFetcher
                    _deep_email_fetcher = PlaywrightFetcher()
                for site_url in site_email.extract_site_links_from_about(html):
                    try:
                        site_emails = await site_email.find_emails_on_site(
                            _deep_email_fetcher, site_url, max_pages=3,
                        )
                    except Exception as e:
                        logger.debug("site_email %s error: %s", site_url, e)
                        continue
                    for email in site_emails:
                        emails_info.append({
                            "email": email,
                            "is_business": True,
                            "source_url": site_url,
                        })
                    if emails_info:
                        break

        # 国家推断（邮箱采集或扩展都需要）
        country, country_confidence = normalize.resolve_country(html, description, title)
        products = sorted(candidate["products"])
        # 产品-国家白名单过滤
        # 未知产品（如 "自定义" 关键词来源）无白名单 → 视为全合格，避免被误杀
        qualified = [
            p for p in products
            if country == "Unknown"
            or p not in allowedByProduct
            or country in allowedByProduct[p]
        ]
        # 没有合格产品的频道跳过（不进库，节省存储）
        if not qualified:
            done += 1
            if on_progress and (done % 5 == 0 or done == total):
                on_progress(done, total, "enrich")
            return

        # 多平台扩展（可选）：从 about 抽 IG/TikTok/X
        socials: list[dict] = []
        if enable_enrich:
            socials = enrich.extract_profiles(html)

        # 发出 YouTube 本平台行（每邮箱一行；无邮箱则一行无 contact_email）
        def _make_yt_row(email_info: dict | None) -> dict:
            return normalize.build_candidate_row(
                platform="YouTube",
                account=handle.lstrip("/@"),
                profile_url=f"https://www.youtube.com{handle}",
                contact_email=email_info["email"] if email_info else None,
                email_is_business=email_info["is_business"] if email_info else False,
                email_source_url=email_info["source_url"] if email_info else None,
                country=country,
                country_confidence=country_confidence,
                followers=followers,
                language="en",
                account_type=youtube.channel_type(title, description),
                fit_products=qualified,
                recommend_product=qualified[0] if qualified else None,
                discovery_queries=sorted(candidate["queries"]),
                discovery_method="平台关键词发现",
                related_youtube=handle.lstrip("/@"),
                yt_about_source=about_url,
            )

        if emails_info:
            for ei in emails_info:
                _emit(_make_yt_row(ei))
        elif enable_email:
            # 开了邮箱采集但没邮箱：仍进大数据库（无邮箱不晋升发信池）
            _emit(_make_yt_row(None))
        else:
            # 没开邮箱采集：只发多平台扩展行，YouTube 本身不进库
            _emit(_make_yt_row(None))

        # 多平台扩展行（IG/TikTok/X），无邮箱
        for social in socials:
            _emit(normalize.build_candidate_row(
                platform=social["platform"],
                account=social["handle"],
                profile_url=social["profile_url"],
                contact_email=None,
                country=country,
                country_confidence=country_confidence,
                fit_products=qualified,
                recommend_product=qualified[0] if qualified else None,
                discovery_queries=sorted(candidate["queries"]),
                discovery_method="YouTube公开简介外链",
                related_youtube=handle.lstrip("/@"),
                yt_about_source=about_url,
            ))

        done += 1
        if on_progress and (done % 5 == 0 or done == total):
            on_progress(done, total, "enrich")

    concurrency = getattr(settings, "CRAWLER_MAX_CONCURRENCY_CHANNEL", 6)
    await gather_pool(candidates, concurrency, _process)
    if on_progress:
        on_progress(total, total, "enrich")
    return out_rows


async def _mx_validate(fetcher, rows: list[dict], on_progress: ProgressFn | None) -> None:
    """阶段 4：对有邮箱的行做 MX 校验，结果写回行的中间字段。

    无 contact_email 的行跳过。MX 无效的行仍保留（只是不晋升发信池），
    但这里会标注 email_mx_valid，供入库前过滤或留档。
    """
    # 收集去重邮箱域
    domains: dict[str, bool | None] = {}
    for r in rows:
        email = r.get("contact_email")
        if email and "@" in email:
            domain = email.rsplit("@", 1)[-1].lower()
            if domain not in domains:
                domains[domain] = None

    domain_list = list(domains.keys())
    done = 0

    async def _check(domain: str) -> None:
        nonlocal done
        domains[domain] = await fetcher.resolve_mx(domain)
        done += 1
        if on_progress and done % 20 == 0:
            on_progress(done, len(domain_list), "mx")

    concurrency = getattr(settings, "CRAWLER_MAX_CONCURRENCY_MX", 30)
    await gather_pool(domain_list, concurrency, _check)

    # 把域校验结果写回每行
    for r in rows:
        email = r.get("contact_email")
        if email and "@" in email:
            domain = email.rsplit("@", 1)[-1].lower()
            r["_mx_valid"] = domains.get(domain)
        else:
            r["_mx_valid"] = None

    if on_progress:
        on_progress(len(domain_list), len(domain_list), "mx")


def _finalize_rows(rows: list[dict]) -> list[dict]:
    """把 _enrich_and_collect_emails 产出的中间行清洗为可入库的 candidate dict。

    - 把 _mx_valid 转进 email_verify_status / collect_status
    - MX 无效的行：contact_email 置空（不晋升发信池），但保留进大数据库
    - 移除中间字段（_mx_valid / products / queries）
    """
    cleaned: list[dict] = []
    for r in rows:
        mx = r.pop("_mx_valid", None)
        if r.get("contact_email") and mx is False:
            # MX 无效：邮箱不进发信池，但账号进大数据库
            r["email_crawler"] = r["contact_email"]  # 留档
            r["contact_email"] = None
            r["email_verify_status"] = "无MX记录"
            r["email_status"] = None
            r["collect_status"] = "无MX记录"
        elif r.get("contact_email") and mx is True:
            r["email_verify_status"] = "MX有效"
        cleaned.append(r)
    return cleaned


async def _run_async(
    products: list[str],
    *,
    enable_enrich: bool,
    enable_email: bool,
    enable_deep_email: bool,
    on_progress: ProgressFn | None,
    db: Optional[Session],
    batch: str,
    commit: bool,
    custom_queries: list[str] | None = None,
    custom_product_terms: dict[str, list[str]] | None = None,
) -> dict:
    """异步执行全流水线。"""
    fetcher = HttpxFetcher()
    custom_queries = custom_queries or []
    stats: dict[str, Any] = {
        "products": products,
        "custom_queries": len(custom_queries),
        "enable_enrich": enable_enrich,
        "enable_email": enable_email,
        "enable_deep_email": enable_deep_email,
        "batch": batch,
        "started_at": datetime.utcnow().isoformat(),
    }
    try:
        # 阶段 1：发现（产品词表 + 自定义关键词）
        candidates = await _discover(
            fetcher, products, on_progress, custom_queries, custom_product_terms
        )
        stats["discovered"] = len(candidates)

        # 阶段 2+3：扩展 + 邮箱
        rows = await _enrich_and_collect_emails(
            fetcher, candidates,
            enable_enrich=enable_enrich,
            enable_email=enable_email,
            enable_deep_email=enable_deep_email,
            on_progress=on_progress,
        )
        stats["raw_rows"] = len(rows)
        stats["with_email"] = sum(1 for r in rows if r.get("contact_email"))

        # 合并正向种子（多平台扩展产物的一部分）
        if enable_enrich:
            for platform, handle, product, note in positiveSeeds:
                rows.append(normalize.build_candidate_row(
                    platform=platform,
                    account=handle,
                    profile_url=enrich._profile_url(platform, handle),
                    contact_email=None,
                    fit_products=[product],
                    recommend_product=product,
                    discovery_queries=[note],
                    discovery_method="需求表正向种子",
                    related_youtube=handle if platform == "YouTube" else None,
                ))

        # 阶段 4：MX 校验（仅对有邮箱的行）
        if enable_email and any(r.get("contact_email") for r in rows):
            await _mx_validate(fetcher, rows, on_progress)
        stats["mx_validated"] = sum(1 for r in rows if r.get("_mx_valid") is True)

        # 阶段 5：清洗 + 入库
        cleaned = _finalize_rows(rows)

        if commit and db is not None:
            from scripts.import_kol_candidate import upsert_candidates
            upsert_stats = upsert_candidates(cleaned, db, batch,
                                             source_label="采集器", commit=True)
            stats.update(upsert_stats)
        else:
            stats["candidate_total"] = len(cleaned)
        stats["finished_at"] = datetime.utcnow().isoformat()
        return stats
    finally:
        await fetcher.aclose()
        # 关闭深度邮箱的 Playwright（如果本次启用过）
        global _deep_email_fetcher
        if _deep_email_fetcher is not None:
            await _deep_email_fetcher.aclose()
            _deep_email_fetcher = None


def run_crawl(
    products: list[str] | None = None,
    *,
    enable_enrich: bool | None = None,
    enable_email: bool | None = None,
    enable_deep_email: bool | None = None,
    on_progress: ProgressFn | None = None,
    commit: bool = False,
    db: Optional[Session] = None,
    batch: str | None = None,
    custom_queries: list[str] | None = None,
    custom_product_terms: dict[str, list[str]] | None = None,
) -> dict:
    """采集总入口（同步包装）。CLI 与 HTTP 端点共用。

    参数：
      products: 内置或持久化自定义产品名列表，如 ["Dreamina", "Pippit"]。
                可为空（当只用 custom_queries 时）。
      custom_queries: 自定义查询词，与产品词表正交合并。
                当 products 为空时，至少要有 custom_queries。
      enable_enrich / enable_email / enable_deep_email: 三档功能开关。
                传 None 时从配置读默认值（CRAWLER_ENABLE_ENRICH/EMAIL/DEEP_EMAIL）。
                这样请求级可临时覆盖，全局有默认。
      on_progress: 进度回调 (done, total, phase)
      commit: 是否写库
      db: 已有 session（HTTP 用）；None 时内部创建（CLI 用）
      batch: 批次标签；None 时按时间生成

    返回统计 dict。
    """
    products = products or []
    custom_queries = custom_queries or []
    if not products and not custom_queries:
        raise ValueError("products 和 custom_queries 不能同时为空")
    if batch is None:
        batch = f"crawl-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    # 请求级 None → 读配置默认值（让开关既可全局配，又可单次覆盖）
    if enable_enrich is None:
        enable_enrich = getattr(settings, "CRAWLER_ENABLE_ENRICH", True)
    if enable_email is None:
        enable_email = getattr(settings, "CRAWLER_ENABLE_EMAIL", True)
    if enable_deep_email is None:
        enable_deep_email = getattr(settings, "CRAWLER_ENABLE_DEEP_EMAIL", False)
    # 深度邮箱依赖邮箱采集开启
    if enable_deep_email and not enable_email:
        enable_deep_email = False

    # 在调用方已有事件循环时（如被 async 端点直接 await），新建独立循环跑。
    # 通常 BackgroundTasks 是同步线程调用，这里直接 get/set 即可。
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 极少发生：在已有运行循环里同步调用。退到新线程跑。
            import threading
            result: dict = {}
            exc: list[BaseException] = []
            def _runner():
                try:
                    new_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(new_loop)
                    result.update(new_loop.run_until_complete(_run_async(
                        products, enable_enrich=enable_enrich,
                        enable_email=enable_email, enable_deep_email=enable_deep_email,
                        on_progress=on_progress,
                        db=db, batch=batch, commit=commit,
                        custom_queries=custom_queries,
                        custom_product_terms=custom_product_terms,
                    )))
                    new_loop.close()
                except BaseException as e:
                    exc.append(e)
            t = threading.Thread(target=_runner)
            t.start()
            t.join()
            if exc:
                raise exc[0]
            return result
    except RuntimeError:
        pass

    return asyncio.run(_run_async(
        products, enable_enrich=enable_enrich, enable_email=enable_email,
        enable_deep_email=enable_deep_email,
        on_progress=on_progress, db=db, batch=batch, commit=commit,
        custom_queries=custom_queries,
        custom_product_terms=custom_product_terms,
    ))
