import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["OPENAI_API_KEY"] = ""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db import Base
from models import FeishuSyncTask, Kol, Message, Thread
from services import feishu_push
from services.quote_detection import extract_money_items
from services.quote_source_analysis import extract_quote_urls


class InMemoryFeishuClient(feishu_push.FeishuClient):
    """Sheet emulator that exercises schema/upsert logic without HTTP."""

    def __init__(self, headers=None, rows=None):
        self.app_id = "id"
        self.app_secret = "secret"
        self.base_url = "https://open.feishu.cn"
        self.spreadsheet_token = "token"
        self.sheet_id = "sheet1"
        self._token = "token"
        self._token_expires_at = float("inf")
        self._sheet_verified = True
        self._schema_cache = None
        self._client = MagicMock()
        self.headers = list(headers or feishu_push.COLUMNS)
        self.rows = [list(row) for row in (rows or [])]

    def _get_values(self, range_a1):
        if range_a1 == "A1:ZZ1":
            return [list(self.headers)]
        # Single-column lookup, data starts on row 2.
        match = feishu_push.re.fullmatch(r"([A-Z]+)2:\1(\d+)", range_a1)
        if match:
            column = self._column_index(match.group(1))
            return [
                [row[column]] if column < len(row) else []
                for row in self.rows
            ]
        # Existing row lookup.
        match = feishu_push.re.fullmatch(r"A(\d+):[A-Z]+\1", range_a1)
        if match:
            index = int(match.group(1)) - 2
            return [list(self.rows[index])] if 0 <= index < len(self.rows) else []
        if feishu_push.re.fullmatch(r"A1:[A-Z]+\d+", range_a1):
            return [list(self.headers)] + [list(row) for row in self.rows]
        raise AssertionError(f"unexpected range: {range_a1}")

    def _put_values(self, range_a1, values):
        if range_a1.startswith("A1:"):
            self.headers = list(values[0])
            return
        match = feishu_push.re.fullmatch(r"A(\d+):[A-Z]+\1", range_a1)
        if not match:
            raise AssertionError(f"unexpected put range: {range_a1}")
        index = int(match.group(1)) - 2
        self.rows[index] = list(values[0])

    def _append_values(self, values):
        self.rows.append(list(values))
        row_num = len(self.rows) + 1
        return {"updatedRange": f"sheet1!A{row_num}:T{row_num}"}

    @staticmethod
    def _column_index(letters):
        value = 0
        for letter in letters:
            value = value * 26 + ord(letter) - 64
        return value - 1


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FeishuPushTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self._orig_session = feishu_push.SessionLocal
        self._orig_settings = feishu_push.settings
        self._orig_client = feishu_push.get_feishu_client
        feishu_push.SessionLocal = self.Session
        self.settings = MagicMock()
        self.settings.feishu_is_configured = True
        self.settings.FEISHU_APP_ID = "id"
        self.settings.FEISHU_APP_SECRET = "secret"
        self.settings.FEISHU_API_BASE = "https://open.feishu.cn/open-apis"
        self.settings.FEISHU_SPREADSHEET_TOKEN = "token"
        self.settings.FEISHU_SHEET_ID = "sheet1"
        feishu_push.settings = self.settings

        db = self.Session()
        kol = Kol(
            name="AiRace99",
            email="Contact.AiRace@gmail.com",
            profile_url="https://www.youtube.com/@AiRace99",
            followers=51300,
            content_category="AI",
            country=None,
            platform=None,
        )
        db.add(kol)
        db.flush()
        thread = Thread(
            kol_id=kol.id,
            subject="Re: collab",
            status="open",
            campaign_name="Pippit-Youtube",
        )
        db.add(thread)
        db.flush()
        old = Message(
            thread_id=thread.id,
            direction="inbound",
            from_email="contact.airace@gmail.com",
            to_email="me@example.com",
            body_text="Dedicated video is USD 2,000",
            received_at=datetime(2026, 7, 22, 10, 0),
            ai_analysis={
                "intent": "high",
                "collaboration_type": "Dedicated Video",
                "platform_rate": "$2,000 USD",
                "complete_quote": "$600 USD dedicated video; $250 USD integration",
                "creator_tags": ["AI工具", "软件教程"],
                "quote": {
                    "items": [
                        {"currency": "USD", "amount": 600},
                        {"currency": "USD", "amount": 250},
                    ]
                },
            },
        )
        latest = Message(
            thread_id=thread.id,
            direction="inbound",
            from_email="contact.airace@gmail.com",
            to_email="me@example.com",
            body_text="Following up",
            received_at=datetime(2026, 7, 23, 12, 0),
            ai_analysis={"summary": "Follow-up without a new price"},
        )
        db.add_all([old, latest])
        db.commit()
        self.kol_id = kol.id
        self.old_id = old.id
        self.latest_id = latest.id
        db.close()

    def tearDown(self):
        feishu_push.SessionLocal = self._orig_session
        feishu_push.settings = self._orig_settings
        feishu_push.get_feishu_client = self._orig_client
        Base.metadata.drop_all(self.engine)

    def _record(self):
        db = self.Session()
        try:
            return feishu_push._build_record(db.get(Message, self.latest_id))
        finally:
            db.close()

    def test_record_is_complete_and_aggregates_older_message(self):
        record = self._record()
        self.assertEqual(record["达人主要链接"], "https://www.youtube.com/@AiRace99")
        # 达人标签改为固定枚举派生（赛道+平台+量级），不再用 AI 自由文本。
        # fixture: content_category=AI→科技/AI, profile_url=youtube→YouTube, followers=51300→万粉。
        self.assertEqual(record["达人标签"], "科技/AI、YouTube、万粉")
        self.assertEqual(record["回信所属项目"], "Pippit-Youtube")
        self.assertEqual(record["平台"], "YouTube")
        self.assertEqual(record["粉丝数"], 51300)
        self.assertEqual(record["应合全链路国家、设备、程别"], "未确认")
        self.assertEqual(record["可接受接入方式"], "Dedicated Video")
        self.assertEqual(
            record["完整报价"],
            "$600 USD dedicated video; $250 USD integration",
        )
        self.assertEqual(record["最低报价"], "$250")
        self.assertEqual(record["意向"], "high")
        self.assertIn("2026-07-23", record["回信时间"])
        self.assertEqual(record["邮箱"], "contact.airace@gmail.com")
        self.assertEqual(record["系统KOL ID"], self.kol_id)
        for column in feishu_push.MANAGED_COLUMNS:
            self.assertNotEqual(record[column], "", column)

    def test_quote_amount_does_not_absorb_following_duration_digits(self):
        items = extract_money_items("$2,500 30-120 seconds reel")
        self.assertEqual([item["amount"] for item in items], [2500])

    def test_only_trusted_rate_card_urls_are_selected(self):
        urls = extract_quote_urls(
            "Rates https://workspace.passionfroot.me/creator "
            "and product https://example.com/pricing"
        )
        self.assertEqual(
            urls,
            ["https://workspace.passionfroot.me/creator"],
        )

    def test_operator_columns_remain_empty_in_snapshot(self):
        record = self._record()
        for column in feishu_push.OPERATOR_COLUMNS:
            self.assertEqual(record[column], "")

    def test_reordered_headers_are_mapped_by_name(self):
        headers = list(reversed(feishu_push.COLUMNS))
        client = InMemoryFeishuClient(headers=headers)
        action, row_num = client.upsert_record(self._record())
        self.assertEqual((action, row_num), ("appended", 2))
        schema = client.ensure_schema()
        row = client.rows[0]
        self.assertEqual(row[schema["邮箱"]], "contact.airace@gmail.com")
        self.assertEqual(row[schema["达人名"]], "AiRace99")
        self.assertEqual(row[schema["平台"]], "YouTube")

    def test_update_preserves_operator_owned_cells(self):
        client = InMemoryFeishuClient()
        schema = client.ensure_schema()
        existing = [""] * len(client.headers)
        existing[schema["系统KOL ID"]] = self.kol_id
        existing[schema["邮箱"]] = "old@example.com"
        existing[schema["代理商名称"]] = "Agency A"
        existing[schema["CPM"]] = "12.50"
        client.rows.append(existing)
        action, row_num = client.upsert_record(self._record())
        self.assertEqual((action, row_num), ("updated", 2))
        row = client.rows[0]
        self.assertEqual(row[schema["代理商名称"]], "Agency A")
        self.assertEqual(row[schema["CPM"]], "12.50")
        self.assertEqual(row[schema["邮箱"]], "contact.airace@gmail.com")

    def test_stable_kol_id_wins_when_email_changed(self):
        client = InMemoryFeishuClient()
        schema = client.ensure_schema()
        existing = [""] * len(client.headers)
        existing[schema["系统KOL ID"]] = self.kol_id
        existing[schema["邮箱"]] = "old@example.com"
        client.rows.append(existing)
        action, row_num = client.upsert_record(self._record())
        self.assertEqual((action, row_num), ("updated", 2))
        self.assertEqual(len(client.rows), 1)

    def test_duplicate_email_fails_closed_instead_of_overwriting_arbitrary_row(self):
        client = InMemoryFeishuClient()
        schema = client.ensure_schema()
        for agency in ("Agency A", "Agency B"):
            row = [""] * len(client.headers)
            row[schema["邮箱"]] = "contact.airace@gmail.com"
            row[schema["代理商名称"]] = agency
            client.rows.append(row)
        with self.assertRaises(feishu_push.FeishuSchemaError):
            client.upsert_record(self._record())
        self.assertEqual(
            [row[schema["代理商名称"]] for row in client.rows],
            ["Agency A", "Agency B"],
        )

    def test_missing_system_headers_are_appended(self):
        client = InMemoryFeishuClient(headers=feishu_push.COLUMNS[:18])
        schema = client.ensure_schema()
        self.assertIn("系统KOL ID", schema)
        self.assertIn("CPM", schema)
        self.assertEqual(client.headers[-2:], ["系统KOL ID", "CPM"])

    def test_missing_business_header_fails_closed(self):
        headers = [
            header for header in feishu_push.COLUMNS[:18] if header != "邮箱"
        ]
        client = InMemoryFeishuClient(headers=headers)
        with self.assertRaises(feishu_push.FeishuSchemaError):
            client.ensure_schema()

    def test_invalid_configured_sheet_falls_back_to_first_live_sheet(self):
        client = feishu_push.FeishuClient.__new__(feishu_push.FeishuClient)
        client.sheet_id = "deleted"
        client.spreadsheet_token = "token"
        client._sheet_verified = False
        client._schema_cache = None
        client._authorized_request = MagicMock(
            return_value={
                "code": 0,
                "data": {"sheets": [{"sheet_id": "live", "title": "Sheet1"}]},
            }
        )
        self.assertEqual(client._resolve_sheet_id(), "live")

    def test_enqueue_collapses_multiple_messages_to_one_kol_task(self):
        self.assertTrue(feishu_push.enqueue_message_sync(self.old_id))
        self.assertTrue(feishu_push.enqueue_message_sync(self.latest_id))
        db = self.Session()
        try:
            tasks = db.query(FeishuSyncTask).all()
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0].source_message_id, self.latest_id)
            self.assertEqual(tasks[0].status, "pending")
        finally:
            db.close()

    def test_borrowed_session_defers_commit_to_caller(self):
        db = self.Session()
        try:
            self.assertTrue(feishu_push.enqueue_message_sync(self.latest_id, db=db))
            # Flushed: visible inside the caller's transaction...
            self.assertEqual(db.query(FeishuSyncTask).count(), 1)
            # ...but not committed: the caller can still roll it back.
            db.rollback()
            self.assertEqual(db.query(FeishuSyncTask).count(), 0)
            self.assertTrue(feishu_push.enqueue_message_sync(self.latest_id, db=db))
            db.commit()
        finally:
            db.close()
        check = self.Session()
        try:
            task = check.query(FeishuSyncTask).one()
            self.assertEqual(task.source_message_id, self.latest_id)
        finally:
            check.close()

    def test_borrowed_session_keeps_callers_pending_changes_uncommitted(self):
        db = self.Session()
        try:
            db.get(Kol, self.kol_id).name = "MidRequestEdit"
            self.assertTrue(feishu_push.enqueue_message_sync(self.latest_id, db=db))
            db.rollback()
            self.assertEqual(db.get(Kol, self.kol_id).name, "AiRace99")
        finally:
            db.close()

    def test_reconciliation_queues_latest_message_per_kol(self):
        queued = feishu_push.enqueue_reconciliation()
        self.assertEqual(queued, 1)
        db = self.Session()
        try:
            task = db.query(FeishuSyncTask).one()
            self.assertEqual(task.source_message_id, self.latest_id)
        finally:
            db.close()

    def test_automatic_reply_is_excluded_and_prior_human_reply_remains_source(self):
        db = self.Session()
        try:
            human = db.get(Message, self.latest_id)
            automatic = Message(
                thread_id=human.thread_id,
                direction="inbound",
                from_email="contact.airace@gmail.com",
                to_email="me@example.com",
                body_text="We received your email and will reply in 3-5 business days.",
                received_at=datetime(2026, 7, 24, 12, 0),
                ai_analysis={
                    "intent": "auto_reply",
                    "complete_quote": "$999",
                },
            )
            db.add(automatic)
            db.commit()
            automatic_id = automatic.id
        finally:
            db.close()

        self.assertFalse(feishu_push.enqueue_message_sync(automatic_id))
        self.assertEqual(feishu_push.enqueue_reconciliation(), 1)
        db = self.Session()
        try:
            task = db.query(FeishuSyncTask).one()
            self.assertEqual(task.source_message_id, self.latest_id)
            record = feishu_push._build_record(db.get(Message, self.latest_id))
            self.assertEqual(
                record[feishu_push.COLUMNS[13]],
                "$600 USD dedicated video; $250 USD integration",
            )
            self.assertIn("2026-07-23", record[feishu_push.COLUMNS[17]])
            with self.assertRaises(ValueError):
                feishu_push._build_record(db.get(Message, automatic_id))
        finally:
            db.close()

    def test_process_success_marks_task_synced(self):
        client = InMemoryFeishuClient()
        feishu_push.get_feishu_client = lambda: client
        feishu_push.enqueue_message_sync(self.latest_id)
        result = feishu_push.process_due_tasks()
        self.assertEqual(result["synced"], 1)
        db = self.Session()
        try:
            task = db.query(FeishuSyncTask).one()
            self.assertEqual(task.status, "synced")
            self.assertIsNotNone(task.payload_hash)
            self.assertEqual(task.feishu_row_number, 2)
        finally:
            db.close()

    def test_process_failure_is_persisted_for_retry(self):
        client = MagicMock()
        client.upsert_record.side_effect = RuntimeError("network down")
        feishu_push.get_feishu_client = lambda: client
        feishu_push.enqueue_message_sync(self.latest_id)
        before = datetime.utcnow()
        result = feishu_push.process_due_tasks()
        self.assertEqual(result["failed"], 1)
        db = self.Session()
        try:
            task = db.query(FeishuSyncTask).one()
            self.assertEqual(task.status, "retry")
            self.assertEqual(task.attempt_count, 1)
            self.assertIn("network down", task.last_error)
            self.assertGreater(task.next_retry_at, before + timedelta(seconds=20))
        finally:
            db.close()

    def test_duplicate_failure_becomes_manual_conflict(self):
        client = MagicMock()
        client.upsert_record.side_effect = feishu_push.FeishuSchemaError(
            "飞书存在重复邮箱，拒绝任选一行覆盖"
        )
        feishu_push.get_feishu_client = lambda: client
        feishu_push.enqueue_message_sync(self.latest_id)
        result = feishu_push.process_due_tasks()
        self.assertEqual(result["failed"], 1)
        db = self.Session()
        try:
            task = db.query(FeishuSyncTask).one()
            self.assertEqual(task.status, "conflict")
        finally:
            db.close()
        # Periodic reconciliation keeps the conflict quarantined.
        feishu_push.enqueue_message_sync(self.latest_id)
        db = self.Session()
        try:
            self.assertEqual(db.query(FeishuSyncTask).one().status, "conflict")
        finally:
            db.close()
        self.assertEqual(feishu_push.retry_conflicts(), 1)
        db = self.Session()
        try:
            self.assertEqual(db.query(FeishuSyncTask).one().status, "pending")
        finally:
            db.close()

    def test_disabled_delivery_keeps_pending_task(self):
        self.settings.feishu_is_configured = False
        feishu_push.enqueue_message_sync(self.latest_id)
        result = feishu_push.process_due_tasks()
        self.assertEqual(result["disabled"], 1)
        db = self.Session()
        try:
            self.assertEqual(db.query(FeishuSyncTask).one().status, "pending")
        finally:
            db.close()

    def test_read_only_audit_reports_duplicate_rows_without_pii(self):
        client = InMemoryFeishuClient()
        schema = client.ensure_schema()
        for agency in ("Agency A", "Agency B"):
            row = [""] * len(client.headers)
            row[schema["邮箱"]] = "contact.airace@gmail.com"
            row[schema["代理商名称"]] = agency
            client.rows.append(row)
        feishu_push.get_feishu_client = lambda: client
        result = feishu_push.audit_sheet()
        self.assertEqual(result["database_kols"], 1)
        self.assertEqual(result["duplicate_email_groups"], 1)
        self.assertEqual(result["duplicate_email_rows"], [[2, 3]])
        self.assertNotIn("contact.airace@gmail.com", str(result))


if __name__ == "__main__":
    unittest.main()
