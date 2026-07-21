import os
import unittest

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["OPENAI_API_KEY"] = ""

from api import snov as snov_api
from api.webhook import _event_kind
from db import SessionLocal, init_db
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


class SnovSyncTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        self.original_factory = snov_api.get_snov_client
        snov_api.get_snov_client = lambda: FakeSnovClient()

    def tearDown(self):
        snov_api.get_snov_client = self.original_factory

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
        finally:
            db.close()

    def test_unsafe_attachment_url_is_discarded(self):
        attachment = normalize_attachments(
            {"filename": "bad.html", "url": "javascript:alert(1)"}
        )[0]
        self.assertIsNone(attachment["url"])

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
