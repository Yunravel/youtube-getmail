import logging
import sys
import threading

from youtube_collector.gui import run


def crawler_smoke_test() -> int:
    """Diagnostic used to verify that a packaged build can drive the system browser."""
    try:
        from playwright.sync_api import sync_playwright

        from youtube_collector.crawler import BrowserCrawler

        crawler = BrowserCrawler(logging.getLogger("crawler-smoke"), interval=0)
        with sync_playwright() as playwright:
            browser = crawler._launch_browser(playwright)
            context = browser.new_context(locale="en-US")
            page = context.new_page()
            page.set_default_timeout(30_000)
            results = crawler._search_results(page, "beauty tutorial", 1, threading.Event())
            if not results:
                return 2
            details = crawler._read_channel(page, results[0]["channel_url"])
            context.close()
            browser.close()
            return 0 if details.get("channel_id") else 3
    except Exception:
        logging.exception("crawler smoke test failed")
        return 1


if __name__ == "__main__":
    if "--crawler-smoke-test" in sys.argv:
        raise SystemExit(crawler_smoke_test())
    run()
