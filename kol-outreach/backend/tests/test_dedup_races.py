"""并发"先查再插"竞争的回归测试。

三处唯一约束上的 check-then-insert:并发时两个请求都查不到既有行、
双双插入,后写方撞 IntegrityError。修复后后写方应按 duplicate/更新
处理,不抛 500,session 依旧可用。

竞争窗口在函数内部,无法真实并发复现,测试用"首次查询返回 None、
之后走真实查询"的包装函数模拟检查落空、由数据库真实唯一约束触发冲突。
"""
import os
import unittest
from unittest.mock import MagicMock, patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["OPENAI_API_KEY"] = ""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api import webhook
from db import Base, get_db
from models import FeishuSyncTask, Kol, Message, ScheduledReply, Thread
from services import auto_reply, feishu_push


def _stale_first_lookup(real_lookup):
    """首次调用返回 None(模拟检查时对方尚未提交),之后委托真实查询。"""
    calls = {"count": 0}

    def lookup(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return None
        return real_lookup(*args, **kwargs)

    return lookup


class _SqliteCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def _make_inbound_message(self, db):
        kol = Kol(name="Creator", email="creator@example.com", status="sent")
        db.add(kol)
        db.flush()
        thread = Thread(kol_id=kol.id, subject="Re: collab", status="open")
        db.add(thread)
        db.flush()
        message = Message(
            thread_id=thread.id,
            direction="inbound",
            from_email="creator@example.com",
            to_email="me@example.com",
            subject="Re: collab",
            body_text="Sounds good, my rate is 500 USD.",
            message_id="<inbound-1@example.com>",
        )
        db.add(message)
        db.commit()
        return kol, thread, message


class SnovWebhookDuplicateRaceTest(_SqliteCase):
    """webhook.py: message.message_id 唯一约束上的并发重投。"""

    def setUp(self):
        super().setUp()
        self.session = self.Session()
        app = FastAPI()
        app.include_router(webhook.router)

        def override_get_db():
            yield self.session

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app, raise_server_exceptions=False)

        self._orig_settings = webhook.settings
        settings = MagicMock()
        settings.snov_webhook_is_configured = True
        settings.SNOV_WEBHOOK_TOKEN = "test-token"
        webhook.settings = settings

        self.payload = {
            "event": "sent",
            "to_email": "creator@example.com",
            "prospect_name": "Creator",
            "subject": "Collab invite",
            "body": "Hello!",
            "message_id": "<snov-evt-1@example.com>",
        }

    def tearDown(self):
        webhook.settings = self._orig_settings
        self.session.close()

    def _post(self):
        return self.client.post("/snov?token=test-token", json=self.payload)

    def test_fast_path_still_reports_duplicate(self):
        self.assertEqual(self._post().json()["status"], "accepted")
        second = self._post()
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["status"], "duplicate")

    def test_concurrent_redelivery_returns_duplicate_not_500(self):
        self.assertEqual(self._post().json()["status"], "accepted")

        # 模拟并发窗口:第二个请求的去重检查落空,真实插入撞唯一约束。
        racy = _stale_first_lookup(webhook._existing_message)
        with patch.object(webhook, "_existing_message", side_effect=racy):
            response = self._post()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "duplicate")
        self.assertEqual(response.json()["message_id"], self.payload["message_id"])

        # 只有一行消息,session 未被污染、仍可用。
        rows = self.session.query(Message).filter(
            Message.message_id == self.payload["message_id"]
        ).all()
        self.assertEqual(len(rows), 1)
        self.session.query(Kol).count()
        self.session.commit()

    def test_non_duplicate_integrity_error_still_raises(self):
        """复查确认不是重复投递时,IntegrityError 不应被吞掉。"""
        self.assertEqual(self._post().json()["status"], "accepted")
        with patch.object(webhook, "_existing_message", return_value=None):
            response = self._post()
        self.assertEqual(response.status_code, 500)


class FeishuEnqueueRaceTest(_SqliteCase):
    """feishu_push.enqueue_message_sync: feishu_sync_task.kol_id 唯一约束。"""

    def setUp(self):
        super().setUp()
        self._orig_session_factory = feishu_push.SessionLocal
        feishu_push.SessionLocal = self.Session
        db = self.Session()
        self.kol, self.thread, self.message = self._make_inbound_message(db)
        self.kol_id = self.kol.id
        self.message_id = self.message.id
        db.close()

    def tearDown(self):
        feishu_push.SessionLocal = self._orig_session_factory

    def _insert_winner(self, status="synced"):
        db = self.Session()
        db.add(FeishuSyncTask(
            kol_id=self.kol_id,
            source_message_id=self.message_id,
            status=status,
        ))
        db.commit()
        db.close()

    def test_concurrent_enqueue_becomes_update(self):
        self._insert_winner(status="synced")

        racy = _stale_first_lookup(feishu_push._sync_task_for_kol)
        with patch.object(feishu_push, "_sync_task_for_kol", side_effect=racy):
            self.assertTrue(feishu_push.enqueue_message_sync(self.message_id))

        db = self.Session()
        tasks = db.query(FeishuSyncTask).filter(
            FeishuSyncTask.kol_id == self.kol_id
        ).all()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].status, "pending")
        self.assertEqual(tasks[0].source_message_id, self.message_id)
        db.close()

    def test_concurrent_enqueue_preserves_conflict_status(self):
        self._insert_winner(status="conflict")

        racy = _stale_first_lookup(feishu_push._sync_task_for_kol)
        with patch.object(feishu_push, "_sync_task_for_kol", side_effect=racy):
            self.assertTrue(feishu_push.enqueue_message_sync(self.message_id))

        db = self.Session()
        task = db.query(FeishuSyncTask).filter(
            FeishuSyncTask.kol_id == self.kol_id
        ).one()
        self.assertEqual(task.status, "conflict")
        db.close()

    def test_borrowed_session_outer_state_survives_savepoint_rollback(self):
        self._insert_winner(status="synced")

        session = self.Session()
        kol = session.get(Kol, self.kol_id)
        kol.name = "Creator Renamed"  # 外层事务里未提交的改动

        racy = _stale_first_lookup(feishu_push._sync_task_for_kol)
        with patch.object(feishu_push, "_sync_task_for_kol", side_effect=racy):
            self.assertTrue(
                feishu_push.enqueue_message_sync(self.message_id, db=session)
            )

        # SAVEPOINT 回滚只撤销冲突插入,外层改动不丢;session 仍可用。
        self.assertEqual(session.get(Kol, self.kol_id).name, "Creator Renamed")
        self.assertEqual(
            session.query(FeishuSyncTask).filter(
                FeishuSyncTask.kol_id == self.kol_id
            ).count(),
            1,
        )
        # 显式提交:无论 enqueue 对借用 session 是 commit 还是仅 flush,
        # 外层改动都应能最终落库。
        session.commit()
        session.close()

        db = self.Session()
        self.assertEqual(db.get(Kol, self.kol_id).name, "Creator Renamed")
        db.close()


class AutoReplyUpsertRaceTest(_SqliteCase):
    """auto_reply._upsert_task: scheduled_reply.source_message_id 唯一约束。"""

    def test_concurrent_upsert_updates_winner_row(self):
        db = self.Session()
        kol, thread, message = self._make_inbound_message(db)

        winner_db = self.Session()
        winner_db.add(ScheduledReply(
            thread_id=thread.id,
            source_message_id=message.id,
            status="queued",
        ))
        winner_db.commit()
        winner_id = winner_db.query(ScheduledReply).one().id
        winner_db.close()

        message.is_read = True  # 调用方事务里未提交的其他改动

        racy = _stale_first_lookup(auto_reply._find_task)
        with patch.object(auto_reply, "_find_task", side_effect=racy):
            task = auto_reply._upsert_task(
                db, message, "manual_review", error_message="并发复核"
            )

        self.assertEqual(task.id, winner_id)
        self.assertEqual(task.status, "manual_review")
        self.assertEqual(task.error_message, "并发复核")
        db.commit()  # session 未被污染,提交成功

        check = self.Session()
        rows = check.query(ScheduledReply).filter(
            ScheduledReply.source_message_id == message.id
        ).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, "manual_review")
        self.assertTrue(check.get(Message, message.id).is_read)
        check.close()
        db.close()


if __name__ == "__main__":
    unittest.main()
