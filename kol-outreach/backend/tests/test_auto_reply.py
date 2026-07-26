import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["OPENAI_API_KEY"] = ""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from config import settings
from db import Base
from models import (
    AutoReplyTemplate, Kol, MailboxCredential, Message, ScheduledReply,
    SendLog, Thread,
)
from services import auto_reply
from services.quote_detection import detect_quote, extract_money_items
from services.smtp_sender import AmbiguousDeliveryError
from fastapi import BackgroundTasks
from fastapi import HTTPException
from api.auto_replies import ManualReplyIn, create_manual_reply


class QuoteDetectionTest(unittest.TestCase):
    def test_explicit_multi_currency_quote(self):
        result = detect_quote(
            "Dedicated YouTube video: USD 1,200\nInstagram package £750\nNo hidden fees."
        )
        self.assertTrue(result["confirmed"])
        self.assertEqual(
            {(item["currency"], item["amount"]) for item in result["items"]},
            {("USD", 1200), ("GBP", 750)},
        )

    def test_vague_rate_card_is_not_confirmed(self):
        result = detect_quote("Please see my rate card attached.")
        self.assertFalse(result["confirmed"])
        self.assertTrue(result["awaiting_attachment"])

    def test_amount_without_currency_is_not_a_quote(self):
        self.assertEqual(extract_money_items("A video would be 500."), [])


class AutoReplyFlowTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.original_factory = auto_reply.SessionLocal
        auto_reply.SessionLocal = self.Session
        self.db = self.Session()
        self._seed()

    def tearDown(self):
        auto_reply.SessionLocal = self.original_factory
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _seed(self):
        now = datetime.utcnow()
        kol = Kol(name="Creator", email="creator@example.com")
        self.db.add(kol)
        self.db.flush()
        thread = Thread(kol_id=kol.id, subject="Collaboration", campaign_id="campaign-1")
        self.db.add(thread)
        self.db.flush()
        source = Message(
            thread_id=thread.id, direction="inbound", from_email=kol.email,
            to_email="team@example.com", subject="Re: Collaboration",
            body_text="Dedicated video: USD 500", message_id="provider-1",
            rfc_message_id="creator-message@example.com", references="<first@example.com>",
            ai_analysis={"intent": "high", "key_questions": []}, received_at=now,
        )
        credential = MailboxCredential(
            email="team@example.com", encrypted_password="encrypted", enabled=True,
            smtp_verified_at=now,
        )
        template = AutoReplyTemplate(
            scope_key="__global__", campaign_name="Default", enabled=True,
            auto_send_enabled=True,
            subject_template="Re: {{ original_subject }}",
            body_template="Hi {{ creator_name }},\n{{ quote_summary }}\nWe will review internally.\n{{ signature }}",
            signature="Team",
        )
        self.db.add_all([source, credential, template])
        self.db.commit()
        self.source_id = source.id

    # ---------- 测试辅助 ----------

    def _task_row(self, source_id=None):
        """从独立 session 重读该来源邮件的任务行（detached 但字段已加载）。"""
        db = self.Session()
        try:
            return db.query(ScheduledReply).filter(
                ScheduledReply.source_message_id == (source_id or self.source_id)
            ).one()
        finally:
            db.close()

    def _reload_task(self, task_id):
        db = self.Session()
        try:
            return db.get(ScheduledReply, task_id)
        finally:
            db.close()

    def _update_task(self, task_id, **fields):
        db = self.Session()
        try:
            db.query(ScheduledReply).filter(ScheduledReply.id == task_id).update(
                fields, synchronize_session=False
            )
            db.commit()
        finally:
            db.close()

    def _set_source_body(self, body_text, source_id=None):
        db = self.Session()
        try:
            db.get(Message, source_id or self.source_id).body_text = body_text
            db.commit()
        finally:
            db.close()

    def _make_task_due(self, task_id):
        self._update_task(
            task_id,
            scheduled_at=datetime.utcnow() - timedelta(seconds=1),
            deadline_at=datetime.utcnow() + timedelta(minutes=30),
        )

    def _make_due_task(self):
        task = auto_reply.evaluate_message_for_auto_reply(self.source_id)
        self._make_task_due(task.id)
        return task.id

    def _seed_second_thread(self):
        db = self.Session()
        try:
            kol = Kol(name="Creator Two", email="creator2@example.com")
            db.add(kol)
            db.flush()
            thread = Thread(kol_id=kol.id, subject="Collab 2", campaign_id="campaign-1")
            db.add(thread)
            db.flush()
            source = Message(
                thread_id=thread.id, direction="inbound", from_email=kol.email,
                to_email="team@example.com", subject="Re: Collab 2",
                body_text="Instagram package: USD 800", message_id="provider-2",
                rfc_message_id="creator2-message@example.com",
                ai_analysis={"intent": "high", "key_questions": []},
                received_at=datetime.utcnow(),
            )
            db.add(source)
            db.commit()
            return source.id
        finally:
            db.close()

    def test_evaluate_is_idempotent_and_schedules_in_window(self):
        task = auto_reply.evaluate_message_for_auto_reply(self.source_id)
        self.assertIsNotNone(task)
        self.assertEqual(task.status, "queued")
        source = self.db.get(Message, self.source_id)
        delay = task.scheduled_at - source.received_at
        self.assertGreaterEqual(delay, timedelta(minutes=60))
        self.assertLessEqual(delay, timedelta(minutes=120))
        again = auto_reply.evaluate_message_for_auto_reply(self.source_id)
        self.assertEqual(task.id, again.id)
        self.assertEqual(self.db.query(ScheduledReply).count(), 1)

    def test_mocked_smtp_creates_real_outbound_only_after_success(self):
        task = auto_reply.evaluate_message_for_auto_reply(self.source_id)
        db = self.Session()
        queued = db.get(ScheduledReply, task.id)
        queued.scheduled_at = datetime.utcnow() - timedelta(seconds=1)
        queued.deadline_at = datetime.utcnow() + timedelta(minutes=30)
        db.commit()
        db.close()

        captured = {}

        def fake_send(credential, message):
            # BUG A 回归探针：真实 send_message/_connect 会读取这些凭据属性。
            # 修复前传入的是 commit 后过期、session close 后 detached 的 ORM 对象，
            # 读取任意属性即抛 DetachedInstanceError。
            captured["credential"] = (
                credential.email, credential.encrypted_password,
                credential.smtp_host, credential.smtp_port, credential.smtp_use_ssl,
            )
            captured["message"] = message

        with patch.object(auto_reply, "send_message", side_effect=fake_send):
            auto_reply.send_due_task(task.id)

        db = self.Session()
        sent_task = db.get(ScheduledReply, task.id)
        self.assertEqual(sent_task.status, "sent")
        outbound = db.query(Message).filter(Message.direction == "outbound").one()
        self.assertEqual(outbound.from_email, "team@example.com")
        self.assertEqual(outbound.in_reply_to, "creator-message@example.com")
        self.assertIn("<creator-message@example.com>", outbound.references)
        self.assertEqual(db.query(SendLog).filter(SendLog.status == "sent").count(), 1)
        self.assertEqual(str(captured["message"]["To"]), "creator@example.com")
        self.assertEqual(
            captured["credential"],
            ("team@example.com", "encrypted", "smtp.gmail.com", 465, True),
        )
        db.close()

    def test_new_inbound_supersedes_queued_task(self):
        task = auto_reply.evaluate_message_for_auto_reply(self.source_id)
        db = self.Session()
        count = auto_reply.supersede_thread_tasks(db, task.thread_id, source_message_id=999)
        db.commit()
        self.assertEqual(count, 1)
        self.assertEqual(db.get(ScheduledReply, task.id).status, "superseded")
        db.close()

    def test_ambiguous_delivery_is_not_retried_or_recorded_as_sent(self):
        task = auto_reply.evaluate_message_for_auto_reply(self.source_id)
        db = self.Session()
        queued = db.get(ScheduledReply, task.id)
        queued.scheduled_at = datetime.utcnow() - timedelta(seconds=1)
        queued.deadline_at = datetime.utcnow() + timedelta(minutes=30)
        db.commit()
        db.close()

        with patch.object(
            auto_reply, "send_message", side_effect=AmbiguousDeliveryError("connection lost after DATA")
        ):
            auto_reply.send_due_task(task.id)

        db = self.Session()
        result = db.get(ScheduledReply, task.id)
        self.assertEqual(result.status, "manual_review")
        self.assertEqual(result.retry_count, 0)
        self.assertEqual(db.query(Message).filter(Message.direction == "outbound").count(), 0)
        self.assertEqual(db.query(SendLog).filter(SendLog.status == "sent").count(), 0)
        db.close()

    def test_operator_can_create_manual_scheduled_reply_without_quote_task(self):
        source = self.db.get(Message, self.source_id)
        source.body_text = "Thanks, I am interested."
        self.db.query(ScheduledReply).delete()
        self.db.commit()
        scheduled_at = datetime.utcnow() + timedelta(days=2)

        result = create_manual_reply(
            source.thread_id,
            ManualReplyIn(
                subject="Re: Collaboration",
                body="Thanks for your reply. We will follow up shortly.",
                scheduled_at=scheduled_at,
            ),
            BackgroundTasks(),
            self.db,
        )

        self.assertEqual(result["status"], "queued")
        self.assertTrue(result["manual_override"])
        task = self.db.get(ScheduledReply, result["id"])
        self.assertIsNone(task.deadline_at)
        self.assertTrue(task.quote_snapshot["manual"])

    def test_manual_reply_reports_unverified_smtp_separately(self):
        source = self.db.get(Message, self.source_id)
        credential = self.db.query(MailboxCredential).filter_by(email=source.to_email).one()
        credential.smtp_verified_at = None
        self.db.commit()

        with self.assertRaises(HTTPException) as caught:
            create_manual_reply(
                source.thread_id,
                ManualReplyIn(
                    subject="Re: Collaboration",
                    body="Thanks for your reply.",
                    send_now=True,
                ),
                BackgroundTasks(),
                self.db,
            )
        self.assertIn("尚未通过 SMTP 测试", caught.exception.detail)

    def test_manual_reply_reports_unknown_recipient_separately(self):
        source = self.db.get(Message, self.source_id)
        source.to_email = "unknown@snov.local"
        self.db.commit()

        with self.assertRaises(HTTPException) as caught:
            create_manual_reply(
                source.thread_id,
                ManualReplyIn(
                    subject="Re: Collaboration",
                    body="Thanks for your reply.",
                    send_now=True,
                ),
                BackgroundTasks(),
                self.db,
            )
        self.assertIn("无法识别", caught.exception.detail)

    # ---------- BUG 回归：重评估不得复活终态任务 / 覆写运营内容 ----------

    def test_reevaluate_keeps_sent_and_cancelled_tasks_final(self):
        """attachment_sync 周期重评估不得把 sent/cancelled 任务复活成 queued。"""
        task = auto_reply.evaluate_message_for_auto_reply(self.source_id)
        for final_status in ("sent", "cancelled"):
            with self.subTest(status=final_status):
                self._update_task(task.id, status=final_status, error_message="operator marker")
                again = auto_reply.evaluate_message_for_auto_reply(self.source_id)
                self.assertIsNotNone(again)
                self.assertEqual(again.id, task.id)
                reloaded = self._reload_task(task.id)
                self.assertEqual(reloaded.status, final_status)
                self.assertEqual(reloaded.error_message, "operator marker")
        db = self.Session()
        self.assertEqual(db.query(ScheduledReply).count(), 1)
        db.close()

    def test_reevaluate_preserves_operator_edited_draft(self):
        """运营编辑过（edited_at 非空）的草稿与排期不得被模板重渲染覆盖。"""
        task = auto_reply.evaluate_message_for_auto_reply(self.source_id)
        keep_time = datetime.utcnow().replace(microsecond=0) + timedelta(hours=6)
        self._update_task(
            task.id,
            draft_subject="Operator subject",
            draft_body="Operator body, no amounts here.",
            scheduled_at=keep_time,
            edited_at=datetime.utcnow(),
        )
        auto_reply.evaluate_message_for_auto_reply(self.source_id)
        reloaded = self._reload_task(task.id)
        self.assertEqual(reloaded.status, "queued")
        self.assertEqual(reloaded.draft_subject, "Operator subject")
        self.assertEqual(reloaded.draft_body, "Operator body, no amounts here.")
        self.assertEqual(reloaded.scheduled_at, keep_time)

    def test_reevaluate_preserves_manual_override_schedule(self):
        """运营手动排期（manual_override=True）的时间安排不得被重评估改写。"""
        task = auto_reply.evaluate_message_for_auto_reply(self.source_id)
        keep_time = datetime.utcnow().replace(microsecond=0) + timedelta(days=2)
        self._update_task(task.id, manual_override=True, scheduled_at=keep_time)
        auto_reply.evaluate_message_for_auto_reply(self.source_id)
        reloaded = self._reload_task(task.id)
        self.assertEqual(reloaded.status, "queued")
        self.assertEqual(reloaded.scheduled_at, keep_time)

    def test_awaiting_attachment_still_upgrades_to_queued_on_reevaluate(self):
        """守卫不得矫枉过正：报价补齐后的重评估仍要把 awaiting_attachment 升级为 queued。"""
        self._set_source_body("My rate card is attached.")
        auto_reply.evaluate_message_for_auto_reply(self.source_id)
        first = self._task_row()
        self.assertEqual(first.status, "awaiting_attachment")

        self._set_source_body("Dedicated video: USD 500")
        auto_reply.evaluate_message_for_auto_reply(self.source_id)
        upgraded = self._task_row()
        self.assertEqual(upgraded.id, first.id)
        self.assertEqual(upgraded.status, "queued")
        self.assertTrue(upgraded.draft_subject)
        self.assertTrue(upgraded.draft_body)

    def test_estimated_received_time_blocks_auto_send(self):
        """received_at 为入库推算值时，确认报价只能进 manual_review，不得自动排队外发。"""
        db = self.Session()
        db.get(Message, self.source_id).received_at_estimated = True
        db.commit()
        db.close()

        auto_reply.evaluate_message_for_auto_reply(self.source_id)
        row = self._task_row()
        self.assertEqual(row.status, "manual_review")
        self.assertIn("入库推算值", row.error_message)

    # ---------- BUG 回归：发送阶段的原子抢占与批次隔离 ----------

    def test_send_due_task_skips_task_already_claimed_as_sending(self):
        """任务已处于 sending（被并发方 claim）时，send_due_task 必须直接返回不发。"""
        task_id = self._make_due_task()
        self._update_task(task_id, status="sending")
        sends = []
        with patch.object(auto_reply, "send_message", side_effect=lambda c, m: sends.append(m)):
            auto_reply.send_due_task(task_id)
        self.assertEqual(sends, [])
        self.assertEqual(self._reload_task(task_id).status, "sending")

    def test_conditional_claim_blocks_concurrent_double_send(self):
        """置 sending 必须是条件更新：校验通过后、claim 前被并发方抢走时不得再发。"""
        task_id = self._make_due_task()
        sends = []
        real_daily_count = auto_reply._daily_sent_count

        def hijack_then_count(db, credential):
            # 模拟 /tasks/{id}/send-now 的 BackgroundTask 在调度器校验途中抢先 claim。
            other = self.Session()
            other.query(ScheduledReply).filter(ScheduledReply.id == task_id).update(
                {"status": "sending"}, synchronize_session=False
            )
            other.commit()
            other.close()
            return real_daily_count(db, credential)

        with patch.object(auto_reply, "_daily_sent_count", side_effect=hijack_then_count), \
                patch.object(auto_reply, "send_message", side_effect=lambda c, m: sends.append(m)):
            auto_reply.send_due_task(task_id)

        self.assertEqual(sends, [])
        self.assertEqual(self._reload_task(task_id).status, "sending")

    def test_unexpected_send_error_is_ambiguous_and_batch_continues(self):
        """SMTP 阶段意外异常按投递不确定进 manual_review，且不打断批次其余任务。"""
        first_id = self._make_due_task()
        second_source_id = self._seed_second_thread()
        second = auto_reply.evaluate_message_for_auto_reply(second_source_id)
        self._make_task_due(second.id)

        def fake_send(credential, message):
            if str(message["To"]) == "creator@example.com":
                raise RuntimeError("unexpected bug during SMTP dialogue")

        with patch.object(auto_reply, "send_message", side_effect=fake_send):
            auto_reply.process_due_replies()

        crashed = self._reload_task(first_id)
        self.assertEqual(crashed.status, "manual_review")
        self.assertEqual(crashed.retry_count, 0)
        self.assertIn("投递结果不确定", crashed.error_message)
        self.assertEqual(self._reload_task(second.id).status, "sent")
        db = self.Session()
        self.assertEqual(db.query(Message).filter(Message.direction == "outbound").count(), 1)
        db.close()

    def test_process_due_replies_survives_send_due_task_crash(self):
        """双保险：send_due_task 本体抛异常也不得打断批次里其余任务。"""
        first_id = self._make_due_task()
        second_source_id = self._seed_second_thread()
        second = auto_reply.evaluate_message_for_auto_reply(second_source_id)
        self._make_task_due(second.id)
        handled = []

        def crashy(task_id):
            if task_id == first_id:
                raise RuntimeError("send_due_task crashed before SMTP")
            handled.append(task_id)

        with patch.object(auto_reply, "send_due_task", side_effect=crashy):
            auto_reply.process_due_replies()

        self.assertEqual(handled, [second.id])


if __name__ == "__main__":
    unittest.main()
