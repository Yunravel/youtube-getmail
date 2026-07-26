import os
import unittest
from datetime import datetime

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["OPENAI_API_KEY"] = ""

from api import snov as snov_api
from api.webhook import _event_kind
from db import Base, SessionLocal, engine, init_db
from models import Message, Thread
from services.attachments import normalize_attachments


class FakeSnovClient:
    def list_campaigns(self):
        return [{"id": 3073708, "campaign": "Creator Outreach"}]

    def get_campaign_replies(self, campaign_id):
        return [
            {
                "campaign": "Creator Outreach",
                "campaignId": 3073708,
                "prospectEmail": "Creator@Example.com",
                "prospectName": "Creator",
                "visitedAt": "2026-07-15T10:00:00Z",
                "emails": [
                    {
                        "emailSubject": "Re: collaboration",
                        "emailBody": "Interested, please send the brief.",
                        "attachments": [
                            {
                                "filename": "brief.pdf",
                                "downloadUrl": "https://files.example.com/brief.pdf",
                                "size": "2048",
                                "mimeType": "application/pdf",
                            }
                        ],
                    }
                ],
            }
        ]

    def list_webhooks(self):
        return []

    def create_webhook(self, event_object, event_action, endpoint_url):
        return {
            "event_object": event_object,
            "event_action": event_action,
            "endpoint_url": endpoint_url,
            "status": "active",
        }


class FakeSnovClientIdenticalReplies(FakeSnovClient):
    """同一 KOL 两封内容完全相同、且 Snov 未给 receivedAt 的回信。"""

    def get_campaign_replies(self, campaign_id):
        reply = {"emailSubject": "Re: collaboration", "emailBody": "Yes, interested."}
        return [
            {
                "campaign": "Creator Outreach",
                "campaignId": 3073708,
                "prospectEmail": "creator@example.com",
                "prospectName": "Creator",
                "emails": [dict(reply), dict(reply)],
            }
        ]


class SnovSyncTest(unittest.TestCase):
    def setUp(self):
        # The full test suite imports ``db`` once, so module-level attempts to
        # replace DATABASE_URL do not create isolated in-memory databases.
        # Reset the shared test schema before each case to prevent rows from
        # earlier test modules affecting ``.one()`` assertions here.
        Base.metadata.drop_all(bind=engine)
        init_db()
        self.original_factory = snov_api.get_snov_client
        snov_api.get_snov_client = lambda: FakeSnovClient()
        # 画像补全 / 飞书推送都是后台线程、用生产 SessionLocal，在内存测试库
        # 里会 "no such table"。测试只验回信落库，patch 成 no-op。
        self.original_enqueue = snov_api._enqueue_kol_enrich
        snov_api._enqueue_kol_enrich = lambda ids: None
        self.original_feishu = snov_api._enqueue_feishu_push
        snov_api._enqueue_feishu_push = lambda ids: None

    def tearDown(self):
        snov_api.get_snov_client = self.original_factory
        snov_api._enqueue_kol_enrich = self.original_enqueue
        snov_api._enqueue_feishu_push = self.original_feishu

    def test_live_v1_shape_is_saved_with_required_fields(self):
        db = SessionLocal()
        try:
            result = snov_api.sync_historical_replies(db)
            self.assertEqual(result["created_messages"], 1)

            thread = db.query(Thread).one()
            message = db.query(Message).one()
            self.assertEqual(thread.campaign_name, "Creator Outreach")
            self.assertEqual(message.from_email, "creator@example.com")
            self.assertEqual(message.subject, "Re: collaboration")
            self.assertIn("Interested", message.body_text)
            self.assertEqual(message.attachments[0]["name"], "brief.pdf")
            self.assertEqual(message.attachments[0]["size"], 2048)
            # visitedAt 是回信时间的合法来源：Z 后缀按 UTC 解析，非推算值。
            self.assertEqual(message.received_at, datetime(2026, 7, 15, 10, 0, 0))
            self.assertFalse(message.received_at_estimated)
        finally:
            db.close()

    def test_unsafe_attachment_url_is_discarded(self):
        attachment = normalize_attachments(
            {"filename": "bad.html", "url": "javascript:alert(1)"}
        )[0]
        self.assertIsNone(attachment["url"])

    def test_identical_replies_are_both_saved(self):
        # 指纹碰撞修复：两封内容完全相同的回信必须都入库，而不是第二封被
        # 误判为 duplicate 丢弃；重跑同步不产生第三条。
        snov_api.get_snov_client = lambda: FakeSnovClientIdenticalReplies()
        db = SessionLocal()
        try:
            result = snov_api.sync_historical_replies(db)
            self.assertEqual(result["created_messages"], 2)
            self.assertEqual(db.query(Message).count(), 2)
            # Snov 未给 receivedAt → received_at 只能填入库时刻，必须带推算标记，
            # 消费端（自动回复新鲜度闸门）据此拒绝当"刚刚收到"处理。
            self.assertTrue(all(
                m.received_at_estimated for m in db.query(Message).all()
            ))

            rerun = snov_api.sync_historical_replies(db)
            self.assertEqual(rerun["created_messages"], 0)
            self.assertEqual(rerun["duplicates"], 2)
            self.assertEqual(db.query(Message).count(), 2)
        finally:
            db.close()

    def test_autoreply_webhook_can_be_created_and_received(self):
        body = snov_api.CreateWebhookIn(
            endpoint_url="https://kol.example.com/api/webhook/snov?token=test-token",
            event_object="campaign_reply",
            event_action="autoreply_received",
        )
        result = snov_api.create_webhook(body)
        self.assertEqual(result["status"], "created")
        self.assertEqual(result["webhook"]["event_action"], "autoreply_received")
        self.assertEqual(_event_kind({
            "event_object": "campaign_reply",
            "event_action": "autoreply_received",
        }), "inbound")


if __name__ == "__main__":
    unittest.main()
