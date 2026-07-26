"""received_at 来源保真（P0 时间解析修复的回归测试）。

三条入库通道（Snov webhook / Snov 历史补拉 / IMAP 补录）统一约定：
  1. 带时区偏移的时间必须换算到 UTC 再入库（库内基准是 naive UTC）。
     旧实现直接丢弃偏移：+03:00 的 15 点被存成 UTC 15 点（实际 UTC 12 点）。
  2. 上游没给时间或值解析不了时，received_at 回退入库时刻，必须同时置
     received_at_estimated=True——自动回复的两小时新鲜度闸门据此拒绝把
     补录的旧信当"刚刚收到"自动放行。
  3. 幂等指纹只吃原始字符串；解析行为的修复不得改变指纹（重跑不重复入库）。
"""
import os
import unittest
from datetime import datetime, timedelta
from unittest import mock

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["OPENAI_API_KEY"] = ""

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from api import snov as snov_api  # noqa: E402
from api import webhook as webhook_api  # noqa: E402
from config import settings  # noqa: E402
from db import Base, SessionLocal, engine, get_db, init_db  # noqa: E402
from models import Message  # noqa: E402
from services.imap_client import ImapMailbox  # noqa: E402

WEBHOOK_TOKEN = "provenance-test-token-123456"


class ParseTimeFunctionsTest(unittest.TestCase):
    """webhook._parse_datetime 与 snov._parse_snov_time 的统一契约。

    两个函数返回 ``(naive UTC datetime, estimated)``；estimated=True 仅在
    值缺失或解析失败时出现，此时时间是入库时刻的推算值。
    """

    FUNCTIONS = (
        ("webhook._parse_datetime", webhook_api._parse_datetime),
        ("snov._parse_snov_time", snov_api._parse_snov_time),
    )

    def test_positive_offset_is_converted_to_utc(self):
        for name, func in self.FUNCTIONS:
            with self.subTest(func=name):
                value, estimated = func("2026-07-26T15:00:00+03:00")
                self.assertEqual(value, datetime(2026, 7, 26, 12, 0, 0))
                self.assertIsNone(value.tzinfo)
                self.assertFalse(estimated)

    def test_negative_offset_is_converted_to_utc(self):
        for name, func in self.FUNCTIONS:
            with self.subTest(func=name):
                value, estimated = func("2026-07-26T15:00:00-07:00")
                self.assertEqual(value, datetime(2026, 7, 26, 22, 0, 0))
                self.assertIsNone(value.tzinfo)
                self.assertFalse(estimated)

    def test_zulu_suffix_kept_as_utc(self):
        for name, func in self.FUNCTIONS:
            with self.subTest(func=name):
                value, estimated = func("2026-07-26T15:00:00Z")
                self.assertEqual(value, datetime(2026, 7, 26, 15, 0, 0))
                self.assertIsNone(value.tzinfo)
                self.assertFalse(estimated)

    def test_naive_string_interpreted_as_utc(self):
        # Snov 的时间基准是 UTC：无偏移的串按 UTC 解释、不做本地时区换算。
        for name, func in self.FUNCTIONS:
            with self.subTest(func=name):
                value, estimated = func("2026-07-26T15:00:00")
                self.assertEqual(value, datetime(2026, 7, 26, 15, 0, 0))
                self.assertFalse(estimated)

    def test_numeric_timestamp_kept(self):
        for name, func in self.FUNCTIONS:
            with self.subTest(func=name):
                value, estimated = func(1753542000)
                self.assertEqual(value, datetime(2025, 7, 26, 15, 0, 0))
                self.assertFalse(estimated)
                float_value, float_estimated = func(1753542000.0)
                self.assertEqual(float_value, datetime(2025, 7, 26, 15, 0, 0))
                self.assertFalse(float_estimated)

    def test_missing_or_garbage_values_are_flagged_estimated(self):
        for name, func in self.FUNCTIONS:
            for raw in (None, "", "definitely-not-a-time", "2026-13-45", {}, []):
                with self.subTest(func=name, raw=raw):
                    before = datetime.utcnow() - timedelta(seconds=10)
                    value, estimated = func(raw)
                    after = datetime.utcnow() + timedelta(seconds=10)
                    self.assertTrue(estimated)
                    self.assertTrue(before <= value <= after)


class ImapParseDateTest(unittest.TestCase):
    """imap_client._parse_date：Date 头解析为 naive UTC。"""

    def test_offset_headers_are_converted_to_utc(self):
        self.assertEqual(
            ImapMailbox._parse_date("Sun, 26 Jul 2026 15:00:00 +0300"),
            datetime(2026, 7, 26, 12, 0, 0),
        )
        self.assertEqual(
            ImapMailbox._parse_date("Sun, 26 Jul 2026 15:00:00 -0700"),
            datetime(2026, 7, 26, 22, 0, 0),
        )
        self.assertEqual(
            ImapMailbox._parse_date("Sun, 26 Jul 2026 15:00:00 +0000"),
            datetime(2026, 7, 26, 15, 0, 0),
        )

    def test_header_without_timezone_is_interpreted_as_utc(self):
        # 旧实现走 mktime_tz：无时区信息时按机器本地时区换算，UTC+8 的
        # Windows 工作站会把 15:00 存成 07:00。修复后无论测试机时区如何，
        # 都必须按 UTC 解释。
        self.assertEqual(
            ImapMailbox._parse_date("Sun, 26 Jul 2026 15:00:00"),
            datetime(2026, 7, 26, 15, 0, 0),
        )

    def test_unparseable_header_returns_none(self):
        # 解析失败返回 None，由调用方按"缺失"处理（estimated=True）。
        self.assertIsNone(ImapMailbox._parse_date(None))
        self.assertIsNone(ImapMailbox._parse_date(""))
        self.assertIsNone(ImapMailbox._parse_date("not a date at all"))


class WebhookReceivedAtProvenanceTest(unittest.TestCase):
    """POST /webhook/snov：时间字段缺失 → estimated；带偏移 → 换算 UTC。"""

    BACKGROUND_PATCH_TARGETS = (
        # 响应后才跑的后台任务与自动回复钩子都 patch 掉：它们各自开
        # SessionLocal()，在 TestClient 工作线程里会拿到全新的空内存库。
        "api.webhook.analyze_inbound_message",
        "services.quote_source_analysis.analyze_and_save_quote_sources",
        "services.kol_enrich.enrich_reply_kol",
        "services.feishu_push.enqueue_message_sync",
        "services.auto_reply.supersede_thread_tasks",
        "services.auto_reply.cancel_for_outbound",
    )

    def setUp(self):
        # 全套测试共享同一个内存库连接，逐例重建 schema 保证隔离。
        Base.metadata.drop_all(bind=engine)
        init_db()
        self._orig_token = settings.SNOV_WEBHOOK_TOKEN
        settings.SNOV_WEBHOOK_TOKEN = WEBHOOK_TOKEN
        for target in self.BACKGROUND_PATCH_TARGETS:
            patcher = mock.patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)

        app = FastAPI()
        app.include_router(webhook_api.router, prefix="/webhook")
        # sqlite 内存库对每个新连接都是一个全新空库，而 TestClient 的请求在
        # 别的线程执行、会从连接池拿到新连接（no such table）。把请求 session
        # 固定绑到主线程已建好 schema 的连接上（check_same_thread=False 允许
        # 跨线程复用；TestClient 串行发请求，无并发竞争）。
        self._connection = engine.connect()
        session_factory = sessionmaker(
            autocommit=False, autoflush=False, bind=self._connection
        )

        def override_get_db():
            db = session_factory()
            try:
                yield db
            finally:
                db.rollback()
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

    def tearDown(self):
        settings.SNOV_WEBHOOK_TOKEN = self._orig_token
        self._connection.close()

    def _post(self, payload: dict):
        return self.client.post(f"/webhook/snov?token={WEBHOOK_TOKEN}", json=payload)

    @staticmethod
    def _reply_payload(**overrides) -> dict:
        payload = {
            "event_object": "campaign_reply",
            "event_action": "received",
            "prospect_email": "creator@example.com",
            "from_email": "creator@example.com",
            "to_email": "ops@brand.example",
            "subject": "Re: collaboration",
            "message": "Happy to collaborate, see my rates.",
            "message_id": "provenance-reply-1",
        }
        payload.update(overrides)
        return payload

    def _fetch_message(self, message_id: str) -> Message:
        db = SessionLocal()
        try:
            return db.query(Message).filter(Message.message_id == message_id).one()
        finally:
            db.close()

    def test_reply_without_time_fields_is_flagged_estimated(self):
        before = datetime.utcnow() - timedelta(seconds=10)
        resp = self._post(self._reply_payload(message_id="no-time-field-1"))
        after = datetime.utcnow() + timedelta(seconds=10)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "accepted")

        message = self._fetch_message("no-time-field-1")
        self.assertEqual(message.direction, "inbound")
        self.assertTrue(message.received_at_estimated)
        self.assertTrue(before <= message.received_at <= after)

    def test_reply_with_offset_time_is_normalized_to_utc(self):
        resp = self._post(self._reply_payload(
            message_id="tz-offset-1",
            received_at="2026-07-26T20:00:00+08:00",
        ))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "accepted")

        message = self._fetch_message("tz-offset-1")
        self.assertEqual(message.received_at, datetime(2026, 7, 26, 12, 0, 0))
        self.assertFalse(message.received_at_estimated)

    def test_sent_event_without_time_fields_is_flagged_estimated(self):
        # outbound sent 事件与 inbound 共用同一条 Message 创建路径，缺时间
        # 字段时同样必须带推算标记。
        resp = self._post({
            "event_object": "campaign_email",
            "event_action": "sent",
            "prospect_email": "creator@example.com",
            "sender_email": "ops@brand.example",
            "subject": "Collaboration invite",
            "message_id": "sent-no-time-1",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "accepted")

        message = self._fetch_message("sent-no-time-1")
        self.assertEqual(message.direction, "outbound")
        self.assertTrue(message.received_at_estimated)


class FakeSnovClientProvenance:
    """两个 prospect：一个无 receivedAt（触发推算回退），一个带 +08:00 偏移。"""

    def list_campaigns(self):
        return [{"id": 990011, "campaign": "Provenance Campaign"}]

    def get_campaign_replies(self, campaign_id):
        return [
            {
                "campaign": "Provenance Campaign",
                "campaignId": 990011,
                "prospectEmail": "no-time@example.com",
                "prospectName": "NoTime",
                "emails": [
                    {"emailSubject": "Re: brief", "emailBody": "Sounds good."}
                ],
            },
            {
                "campaign": "Provenance Campaign",
                "campaignId": 990011,
                "prospectEmail": "with-offset@example.com",
                "prospectName": "WithOffset",
                "emails": [
                    {
                        "emailSubject": "Re: brief",
                        "emailBody": "Interested, sending rates.",
                        "receivedAt": "2026-07-15T18:00:00+08:00",
                    }
                ],
            },
        ]

    def list_webhooks(self):
        return []


class SnovSyncReceivedAtProvenanceTest(unittest.TestCase):
    """历史补拉：estimated 标记 + 时区换算 + 幂等指纹稳定性回归。"""

    def setUp(self):
        Base.metadata.drop_all(bind=engine)
        init_db()
        self._orig_factory = snov_api.get_snov_client
        self._orig_enrich = snov_api._enqueue_kol_enrich
        self._orig_feishu = snov_api._enqueue_feishu_push
        snov_api.get_snov_client = lambda: FakeSnovClientProvenance()
        # 画像补全 / 飞书推送是后台线程 + 生产 SessionLocal，在内存测试库
        # 里会 "no such table"，与 test_snov_sync.py 一致 patch 成 no-op。
        snov_api._enqueue_kol_enrich = lambda ids: None
        snov_api._enqueue_feishu_push = lambda ids: None

    def tearDown(self):
        snov_api.get_snov_client = self._orig_factory
        snov_api._enqueue_kol_enrich = self._orig_enrich
        snov_api._enqueue_feishu_push = self._orig_feishu

    def test_missing_received_at_estimated_and_fingerprint_stable(self):
        db = SessionLocal()
        try:
            before = datetime.utcnow() - timedelta(seconds=10)
            result = snov_api.sync_historical_replies(db)
            after = datetime.utcnow() + timedelta(seconds=10)
            self.assertEqual(result["created_messages"], 2)

            no_time = db.query(Message).filter(
                Message.from_email == "no-time@example.com"
            ).one()
            self.assertTrue(no_time.received_at_estimated)
            self.assertTrue(before <= no_time.received_at <= after)

            with_offset = db.query(Message).filter(
                Message.from_email == "with-offset@example.com"
            ).one()
            self.assertFalse(with_offset.received_at_estimated)
            self.assertEqual(
                with_offset.received_at, datetime(2026, 7, 15, 10, 0, 0)
            )

            # 指纹红线回归：幂等键吃原始 receivedAt 字符串（缺失时为空串），
            # 与解析结果无关。重跑同一批必须全部判重——若有人把解析后的时间
            # （无 receivedAt 时是每次不同的 utcnow）掺进指纹，这里会重复入库。
            rerun = snov_api.sync_historical_replies(db)
            self.assertEqual(rerun["created_messages"], 0)
            self.assertEqual(rerun["duplicates"], 2)
            self.assertEqual(db.query(Message).count(), 2)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
