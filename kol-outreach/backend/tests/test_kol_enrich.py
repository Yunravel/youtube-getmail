import os
import unittest

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["OPENAI_API_KEY"] = ""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db import Base
from models import Kol
from services import kol_enrich


class FakeFetcher:
    """按 URL → HTML 映射返回内容的假抓取器。"""

    def __init__(self, pages: dict[str, str]):
        self._pages = pages

    async def fetch_text(self, url: str) -> str:
        return self._pages.get(url, "")


# 构造一段足够长的 about 页 HTML，描述里嵌入给定邮箱。
def _about_html(handle: str, email: str, subscribers_text: str = "1.23M subscribers") -> str:
    return (
        '<meta name="title" content="Some Channel - YouTube">'
        f'<meta name="description" content="Business: {email}">'
        f'"subscriberCountText":"{subscribers_text}"'
        + ("x" * 6000)  # 撑过 _MIN_ABOUT_LEN 阈值
    )


class KolEnrichTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        # enrich_reply_kol 内部开独立 session，必须替换模块级工厂。
        self._orig_session = kol_enrich.SessionLocal
        kol_enrich.SessionLocal = self.Session
        self._orig_runner = kol_enrich._run_async_safely
        # 测试在普通同步线程里跑，直接跑协程即可，不走 thread-fallback。
        kol_enrich._run_async_safely = lambda coro: __import__("asyncio").run(coro)

    def tearDown(self):
        kol_enrich.SessionLocal = self._orig_session
        kol_enrich._run_async_safely = self._orig_runner
        Base.metadata.drop_all(self.engine)

    def _add_kol(self, **kw) -> Kol:
        db = self.Session()
        defaults = dict(name="TestKol", email="biz@example.com", status="in_conversation")
        defaults.update(kw)
        kol = Kol(**defaults)
        db.add(kol)
        db.commit()
        kol_id = kol.id
        db.close()
        return kol_id

    def _patch_fetcher(self, pages: dict[str, str]):
        kol_enrich._new_fetcher = lambda: FakeFetcher(pages)

    # --- 核心匹配逻辑 ---

    def test_email_match_writes_fields(self):
        kol_id = self._add_kol(name="AiRace99", email="contact.airace@gmail.com")
        self._patch_fetcher({
            "https://www.youtube.com/results?search_query=AiRace99":
                '"canonicalBaseUrl":"/@AiRace99"',
            "https://www.youtube.com/@AiRace99/about":
                _about_html("@AiRace99", "contact.airace@gmail.com", "45K subscribers"),
        })
        kol_enrich.enrich_reply_kol(kol_id)
        db = self.Session()
        kol = db.get(Kol, kol_id)
        self.assertEqual(kol.channel_url, "https://www.youtube.com/@AiRace99")
        self.assertEqual(kol.subscribers, 45000)
        self.assertEqual(kol.source, "reply_enrich")
        db.close()

    def test_no_email_match_does_not_write(self):
        kol_id = self._add_kol(name="AiRace99", email="contact.airace@gmail.com")
        # about 页里是别的邮箱，不命中 → 不回填
        self._patch_fetcher({
            "https://www.youtube.com/results?search_query=AiRace99":
                '"canonicalBaseUrl":"/@AiRace99"',
            "https://www.youtube.com/@AiRace99/about":
                _about_html("@AiRace99", "someone.else@gmail.com"),
        })
        kol_enrich.enrich_reply_kol(kol_id)
        db = self.Session()
        kol = db.get(Kol, kol_id)
        self.assertIsNone(kol.channel_url)
        self.assertEqual(kol.subscribers, 0)
        db.close()

    def test_skips_when_channel_url_already_set(self):
        kol_id = self._add_kol(
            name="AiRace99",
            email="contact.airace@gmail.com",
            channel_url="https://www.youtube.com/@existing",
        )
        # 即使 fetcher 命中，_should_enrich 守卫也该挡住。
        self._patch_fetcher({
            "https://www.youtube.com/results?search_query=AiRace99":
                '"canonicalBaseUrl":"/@AiRace99"',
            "https://www.youtube.com/@AiRace99/about":
                _about_html("@AiRace99", "contact.airace@gmail.com"),
        })
        kol_enrich.enrich_reply_kol(kol_id)
        db = self.Session()
        kol = db.get(Kol, kol_id)
        # 不被覆盖
        self.assertEqual(kol.channel_url, "https://www.youtube.com/@existing")
        self.assertEqual(kol.source, None)
        db.close()

    def test_fill_empty_does_not_overwrite_existing_niche(self):
        # 命中后回填，但已有 niche 的列应保留原值
        kol_id = self._add_kol(name="AiRace99", email="contact.airace@gmail.com", niche="Tech")
        self._patch_fetcher({
            "https://www.youtube.com/results?search_query=AiRace99":
                '"canonicalBaseUrl":"/@AiRace99"',
            "https://www.youtube.com/@AiRace99/about":
                _about_html("@AiRace99", "contact.airace@gmail.com", "45K subscribers"),
        })
        kol_enrich.enrich_reply_kol(kol_id)
        db = self.Session()
        kol = db.get(Kol, kol_id)
        self.assertEqual(kol.channel_url, "https://www.youtube.com/@AiRace99")
        self.assertEqual(kol.subscribers, 45000)
        # niche 已有值，不被覆盖
        self.assertEqual(kol.niche, "Tech")
        db.close()

    def test_skips_blacklisted_status(self):
        kol_id = self._add_kol(name="AiRace99", email="contact.airace@gmail.com", status="blacklisted")
        self._patch_fetcher({
            "https://www.youtube.com/results?search_query=AiRace99":
                '"canonicalBaseUrl":"/@AiRace99"',
            "https://www.youtube.com/@AiRace99/about":
                _about_html("@AiRace99", "contact.airace@gmail.com"),
        })
        kol_enrich.enrich_reply_kol(kol_id)
        db = self.Session()
        kol = db.get(Kol, kol_id)
        self.assertIsNone(kol.channel_url)
        db.close()

    def test_exception_in_fetch_does_not_raise(self):
        kol_id = self._add_kol(name="AiRace99", email="contact.airace@gmail.com")

        def _boom():
            raise RuntimeError("network down")

        kol_enrich._new_fetcher = _boom
        # 不应抛出
        kol_enrich.enrich_reply_kol(kol_id)
        db = self.Session()
        kol = db.get(Kol, kol_id)
        self.assertIsNone(kol.channel_url)
        db.close()


if __name__ == "__main__":
    unittest.main()
