import os
import unittest
from datetime import datetime, timedelta

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["OPENAI_API_KEY"] = ""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.mailbox import (
    MailboxBulkStateIn,
    MailboxStateIn,
    list_mailbox,
    mailbox_filters,
    update_thread_state,
    update_threads_state,
)
from db import Base
from models import Kol, Message, SendLog, Thread


class MailboxTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self._seed()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _seed(self):
        now = datetime(2026, 7, 15, 12, 0, 0)
        kol = Kol(name="Creator", email="creator@example.com")
        other_kol = Kol(name="Pending", email="pending@example.com")
        self.db.add_all([kol, other_kol])
        self.db.flush()

        self.thread_one = Thread(
            kol_id=kol.id,
            subject="Collaboration",
            campaign_id="campaign-one",
            campaign_name="Campaign One",
        )
        self.thread_two = Thread(
            kol_id=kol.id,
            subject="Second project",
            campaign_id="campaign-two",
            campaign_name="Campaign Two",
        )
        self.pending_thread = Thread(
            kol_id=other_kol.id,
            subject="Pending draft",
            campaign_id="campaign-one",
            campaign_name="Campaign One",
        )
        self.db.add_all([self.thread_one, self.thread_two, self.pending_thread])
        self.db.flush()

        self.db.add_all([
            Message(
                thread_id=self.thread_one.id,
                direction="inbound",
                from_email="creator@example.com",
                to_email="team@example.com",
                subject="Re: Collaboration",
                body_text="Interested, please send the brief.",
                message_id="inbound-one",
                attachments=[{"name": "brief.pdf"}],
                is_read=False,
                received_at=now,
            ),
            Message(
                thread_id=self.thread_one.id,
                direction="outbound",
                from_email="team@example.com",
                to_email="creator@example.com",
                subject="Re: Collaboration",
                body_text="Here are the details.",
                message_id="outbound-one",
                is_read=True,
                received_at=now + timedelta(minutes=1),
            ),
            Message(
                thread_id=self.thread_two.id,
                direction="inbound",
                from_email="creator@example.com",
                to_email="other-team@example.com",
                subject="Re: Second project",
                body_text="A unique searchable phrase.",
                message_id="inbound-two",
                is_read=True,
                received_at=now + timedelta(minutes=2),
            ),
            # A local draft has no provider message_id and must never appear in Sent.
            Message(
                thread_id=self.pending_thread.id,
                direction="outbound",
                from_email="(via Instantly)",
                to_email="pending@example.com",
                subject="Pending draft",
                body_text="Not actually sent.",
                message_id=None,
                is_read=True,
                received_at=now + timedelta(minutes=3),
            ),
        ])
        self.db.add(SendLog(
            thread_id=self.pending_thread.id,
            kol_id=other_kol.id,
            provider="instantly",
            status="bounced",
        ))
        self.db.commit()

    def _list(self, folder="inbox", q=None, campaign_id=None, account=None):
        return list_mailbox(
            folder=folder,
            q=q,
            campaign_id=campaign_id,
            account=account,
            page=1,
            page_size=25,
            db=self.db,
        )

    def test_folder_counts_use_real_message_rules(self):
        result = self._list()
        self.assertEqual(result["folder_counts"], {
            "inbox": 2,
            "sent": 1,
            "starred": 0,
            "bounced": 1,
        })
        self.assertEqual(result["total"], len(result["items"]))
        self.assertTrue(any(item["has_attachments"] for item in result["items"]))
        self.assertEqual(self._list(folder="sent")["items"][0]["thread_id"], self.thread_one.id)

    def test_search_campaign_and_account_keep_duplicate_contact_threads_separate(self):
        result = self._list(q="unique searchable")
        self.assertEqual([item["thread_id"] for item in result["items"]], [self.thread_two.id])

        campaign_result = self._list(campaign_id="campaign-one")
        self.assertEqual(campaign_result["total"], 1)

        account_result = self._list(account="other-team@example.com")
        self.assertEqual([item["thread_id"] for item in account_result["items"]], [self.thread_two.id])

        filters = mailbox_filters(self.db)
        self.assertEqual(len(filters["campaigns"]), 2)
        self.assertIn("team@example.com", filters["accounts"])
        self.assertIn("other-team@example.com", filters["accounts"])

    def test_pagination_keeps_total_and_folder_count_consistent(self):
        first_page = list_mailbox(
            folder="inbox",
            q=None,
            campaign_id=None,
            account=None,
            page=1,
            page_size=1,
            db=self.db,
        )
        second_page = list_mailbox(
            folder="inbox",
            q=None,
            campaign_id=None,
            account=None,
            page=2,
            page_size=1,
            db=self.db,
        )
        self.assertEqual(first_page["total"], 2)
        self.assertEqual(first_page["folder_counts"]["inbox"], 2)
        self.assertEqual(len(first_page["items"]), 1)
        self.assertEqual(len(second_page["items"]), 1)
        self.assertNotEqual(
            first_page["items"][0]["thread_id"],
            second_page["items"][0]["thread_id"],
        )

    def test_single_and_bulk_read_star_updates_persist(self):
        update_thread_state(
            self.thread_one.id,
            MailboxStateIn(is_read=True, is_starred=True),
            self.db,
        )
        first = next(
            item for item in self._list(folder="starred")["items"]
            if item["thread_id"] == self.thread_one.id
        )
        self.assertTrue(first["is_read"])
        self.assertTrue(first["is_starred"])

        update_threads_state(
            MailboxBulkStateIn(
                thread_ids=[self.thread_one.id, self.thread_two.id],
                is_read=False,
                is_starred=False,
            ),
            self.db,
        )
        inbox = self._list()["items"]
        self.assertEqual(sum(item["unread_count"] for item in inbox), 2)
        self.assertEqual(self._list(folder="starred")["total"], 0)


if __name__ == "__main__":
    unittest.main()
