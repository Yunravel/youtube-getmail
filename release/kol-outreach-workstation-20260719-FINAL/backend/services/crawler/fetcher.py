"""可插拔抓取器接口与 httpx 实现。

设计目的：把"怎么拿到页面文本 / 怎么校验 MX"与业务解析逻辑解耦。
首期只有 :class:`HttpxFetcher`（裸 httpx + 正则解析，与原 Node 脚本同策略）。
将来要加代理、Playwright 渲染、无头浏览器时，只需新增一个 Fetcher 实现，
``pipeline.py`` 与 ``youtube.py`` / ``enrich.py`` 的业务代码不动。

所有方法返回字符串或布尔；不抛网络异常（失败返回 "" / False，由调用方决定
跳过还是重试），与 Node ``getText`` 失败即跳过的语义一致。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Protocol

import httpx

from config import settings

logger = logging.getLogger(__name__)


class Fetcher(Protocol):
    """抓取器协议。业务代码只依赖这个接口，不绑定具体实现。"""

    async def fetch_text(self, url: str) -> str:
        """抓页面文本。失败返回 ""。"""
        ...

    async def resolve_mx(self, domain: str) -> bool:
        """校验域名的 MX 记录。有返回 True，无/失败返回 False。"""
        ...

    async def aclose(self) -> None:
        """释放底层资源（连接池等）。"""
        ...


class HttpxFetcher:
    """基于 httpx 的抓取器实现。

    - 单页超时、请求间隔（礼貌限速）、UA 从配置读取
    - 429/5xx 自动重试（指数退避），与 Node getText 一致
    - **代理池轮换**：配置 CRAWLER_PROXIES 后，按 round-robin 轮流用每个代理
      发请求；遇到 403/429 立即换下一个代理重试（住宅代理必备：单 IP 被限流
      时自动切下一个）。未配置则直连（仅适合本地开发）。
    - MX 校验用 dnspython（同步调用，包在 to_thread 里避免阻塞事件循环）
    """

    def __init__(
        self,
        *,
        timeout: int | None = None,
        interval: float | None = None,
        user_agent: str | None = None,
        max_retries: int = 3,
        proxies: list[str] | None = None,
    ):
        self._timeout = timeout or getattr(settings, "CRAWLER_REQUEST_TIMEOUT", 20)
        self._interval = interval or getattr(settings, "CRAWLER_REQUEST_INTERVAL", 0.2)
        self._max_retries = max_retries
        # 代理池：从参数或配置取。空列表 = 直连。
        proxy_list = proxies if proxies is not None else settings.crawler_proxies_list
        ua = user_agent or getattr(
            settings, "CRAWLER_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/138 Safari/537.36",
        )
        common_headers = {"accept-language": "en-US,en;q=0.9", "user-agent": ua}

        # 为每个代理建一个独立 client（httpx client 绑定单一 proxy）。
        # 无代理时建一个直连 client。
        self._clients: list[httpx.AsyncClient] = []
        self._proxy_labels: list[str] = []   # 用于日志（只记序号，不记密钥）
        if proxy_list:
            for p in proxy_list:
                try:
                    self._clients.append(httpx.AsyncClient(
                        headers=common_headers,
                        timeout=httpx.Timeout(self._timeout),
                        follow_redirects=True,
                        proxy=p,
                    ))
                    self._proxy_labels.append(f"proxy#{len(self._proxy_labels)}")
                except Exception as e:
                    logger.warning("代理初始化失败（已跳过）: %s", e)
        if not self._clients:
            # 无代理或全部初始化失败 → 直连
            self._clients.append(httpx.AsyncClient(
                headers=common_headers,
                timeout=httpx.Timeout(self._timeout),
                follow_redirects=True,
            ))
            self._proxy_labels.append("direct")
        self._cursor = 0
        self._last_request_at = 0.0

    def _next_client(self) -> httpx.AsyncClient:
        """round-robin 选下一个 client。"""
        client = self._clients[self._cursor % len(self._clients)]
        self._cursor = (self._cursor + 1) % len(self._clients)
        return client

    async def fetch_text(self, url: str) -> str:
        """抓页面。429/5xx/403 重试且每次换代理；其他失败返回 ""。"""
        # 礼貌限速：与上次请求至少间隔 interval
        now = asyncio.get_event_loop().time()
        wait = self._interval - (now - self._last_request_at)
        if wait > 0:
            await asyncio.sleep(wait)

        # 重试次数 = max_retries，但每次换一个 client（代理）
        # 这样代理池越大，单个 IP 承受的请求越分散
        attempts = max(self._max_retries, len(self._clients))
        for attempt in range(attempts + 1):
            client = self._next_client()
            label = self._proxy_labels[self._cursor - 1] if self._proxy_labels else "?"
            try:
                self._last_request_at = asyncio.get_event_loop().time()
                resp = await client.get(url)
                if resp.status_code == 200:
                    return resp.text
                # 403/429/5xx：换代理重试（代理池场景下尤其有效）
                if resp.status_code in (403, 429) or resp.status_code >= 500:
                    if attempt < attempts:
                        await asyncio.sleep(1.2 * (attempt + 1))
                        continue
                # 4xx（除 403/429）等不重试
                logger.debug("fetch %s via %s -> %s", url, label, resp.status_code)
                return ""
            except (httpx.HTTPError, OSError) as e:
                if attempt < attempts:
                    await asyncio.sleep(1.2 * (attempt + 1))
                    continue
                logger.debug("fetch %s via %s error: %s", url, label, e)
                return ""
        return ""

    async def resolve_mx(self, domain: str) -> bool:
        """MX 校验。dnspython 是同步阻塞 API，丢到线程池。"""
        try:
            import dns.resolver
            import dns.exception
        except ImportError:
            logger.warning("dnspython 未安装，MX 校验一律放行")
            return True

        def _check() -> bool:
            try:
                answers = dns.resolver.resolve(domain, "MX", lifetime=3.0)
                return len(answers) > 0
            except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN,
                    dns.exception.Timeout, Exception):
                return False

        return await asyncio.to_thread(_check)

    async def aclose(self) -> None:
        for c in self._clients:
            try:
                await c.aclose()
            except Exception:
                pass


class PlaywrightFetcher:
    """基于 Playwright 的抓取器实现（headless chromium 渲染）。

    与 :class:`HttpxFetcher` 实现同一 ``Fetcher`` 协议，但用真实浏览器加载页面，
    能拿到 JS 渲染后的内容（SPA 站点 / Cloudflare 防护页）。用于官网深度邮箱采集。

    代价：慢（每页 2-3 秒渲染）、重（每实例起一个浏览器进程）。
    所以 **只用于 httpx 抓不到的站点**，不用于 YouTube about（httpx 足够）。

    代理：复用 ``CRAWLER_PROXIES`` 配置的代理池（与 HttpxFetcher 同源），
    每个浏览器 context 绑一个代理。住宅代理是抓 KOL 官网的前提（数据中心 IP
    会被 Cloudflare 等直接重置连接）。

    MX 校验：Playwright 无 DNS API，复用 dnspython（与 HttpxFetcher 同）。
    """

    def __init__(
        self,
        *,
        timeout: int | None = None,
        user_agent: str | None = None,
        proxies: list[str] | None = None,
        headless: bool = True,
    ):
        self._timeout = timeout or getattr(settings, "CRAWLER_REQUEST_TIMEOUT", 20)
        proxy_list = proxies if proxies is not None else settings.crawler_proxies_list
        self._ua = user_agent or getattr(
            settings, "CRAWLER_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/138 Safari/537.36",
        )
        self._headless = headless
        self._proxy_list = proxy_list
        self._cursor = 0
        self._playwright = None
        self._browser = None
        self._contexts: list = []   # 每个 context 绑一个代理，轮换用

    async def _ensure_browser(self):
        """懒启动浏览器（首次 fetch 时才起，避免无 fetch 就开进程）。"""
        if self._browser is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:
            raise RuntimeError(
                "PlaywrightFetcher 需要 playwright：pip install playwright && playwright install chromium"
            ) from e
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self._headless)
        # 为每个代理建一个 context；无代理时建一个直连 context
        if self._proxy_list:
            for p in self._proxy_list:
                try:
                    ctx = await self._browser.new_context(
                        user_agent=self._ua,
                        proxy={"server": p} if not _proxy_has_auth(p)
                        else _proxy_with_auth(p),
                    )
                    self._contexts.append(ctx)
                except Exception as e:
                    logger.warning("playwright 代理 context 初始化失败（跳过）: %s", e)
        if not self._contexts:
            self._contexts.append(await self._browser.new_context(user_agent=self._ua))

    def _next_context(self):
        ctx = self._contexts[self._cursor % len(self._contexts)]
        self._cursor = (self._cursor + 1) % len(self._contexts)
        return ctx

    async def fetch_text(self, url: str) -> str:
        """用浏览器加载页面，返回渲染后的 body 文本。

        渲染策略：domcontentloaded 后再等 2.5 秒让 SPA 把内容挂到 DOM。
        失败（超时/连接重置/被拦）返回 ""。
        """
        await self._ensure_browser()
        ctx = self._next_context()
        page = await ctx.new_page()
        try:
            resp = await page.goto(url, wait_until="domcontentloaded",
                                   timeout=self._timeout * 1000)
            if resp is None or resp.status >= 400:
                logger.debug("playwright fetch %s -> %s", url,
                             resp.status if resp else "no-response")
                return ""
            # 给 SPA 时间渲染
            await page.wait_for_timeout(2500)
            try:
                return await page.inner_text("body")
            except Exception:
                # 有些页面没有标准 body，退回全部 HTML
                return await page.content()
        except Exception as e:
            logger.debug("playwright fetch %s error: %s", url, e)
            return ""
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def resolve_mx(self, domain: str) -> bool:
        """MX 校验：复用 dnspython（同 HttpxFetcher）。"""
        try:
            import dns.resolver
            import dns.exception
        except ImportError:
            return True

        def _check() -> bool:
            try:
                answers = dns.resolver.resolve(domain, "MX", lifetime=3.0)
                return len(answers) > 0
            except Exception:
                return False

        return await asyncio.to_thread(_check)

    async def aclose(self) -> None:
        for ctx in self._contexts:
            try:
                await ctx.close()
            except Exception:
                pass
        self._contexts = []
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None


def _proxy_has_auth(proxy_url: str) -> bool:
    """代理 URL 是否含用户名密码（http://user:pass@host）。"""
    from urllib.parse import urlparse
    try:
        p = urlparse(proxy_url)
        return bool(p.username and p.password)
    except Exception:
        return False


def _proxy_with_auth(proxy_url: str) -> dict:
    """把 http://user:pass@host:port 解析成 Playwright 要的 proxy dict。"""
    from urllib.parse import urlparse
    p = urlparse(proxy_url)
    server = f"{p.scheme}://{p.hostname}:{p.port}"
    return {"server": server, "username": p.username, "password": p.password}


async def gather_pool(items, concurrency, handler):
    """并发池：最多 concurrency 个任务同时跑 handler(item)。

    对应 Node 的 ``pool(items, concurrency, handler)``。
    handler 是 async 函数。用于 discovery/channel/mx 三阶段限速。

    异常处理：handler 抛的异常会被收集并记日志（不再静默吞掉），
    但不会中断整个池——单个 item 失败不影响其他 item。
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def _run(item):
        async with semaphore:
            await handler(item)

    results = await asyncio.gather(*[_run(it) for it in items], return_exceptions=True)
    # 统计并记录失败（return_exceptions=True 把异常当返回值，默认会静默吞掉）
    failures = [r for r in results if isinstance(r, Exception)]
    if failures:
        # 只记第一条详情 + 失败总数，避免日志爆炸
        logger.warning(
            "gather_pool: %d/%d items failed. first error: %s: %s",
            len(failures), len(results),
            type(failures[0]).__name__, failures[0],
        )
    return results
