"""DELETE /api/kols/{id} 级联清理(api/kol.py delete_kol)。

生产 PostgreSQL 上 thread.kol_id 等外键没有 ON DELETE CASCADE,直接删 KOL 会被
外键拒绝(thread_kol_id_fkey → 500)。SQLite 默认不查外键所以开发库看不出来;
这里显式 PRAGMA foreign_keys=ON,让内存库按生产语义强制外键,回归时测试会炸。
"""
import os
import unittest
from datetime import datetime

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["OPENAI_API_KEY"] = ""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api import kol as kol_api
from db import Base, get_db
from models import (
    FeishuSyncTask, Kol, KolEmail, Message, Note, Operator, ProjectAssessment,
    ScheduledReply, SendLog, Thread,
)


class KolDeleteTest(unittest.TestCase):
    def setUp(self):
        # StaticPool 共享单连接,TestClient 工作线程与测试线程看到同一个内存库
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )

        @event.listens_for(self.engine, "connect")
        def _enforce_fk(dbapi_conn, _record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

        app = FastAPI()
        app.include_router(kol_api.router, prefix="/api/kols")

        def _override_get_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = _override_get_db
        self.client = TestClient(app)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _seed_kol_with_children(self) -> int:
        now = datetime.utcnow()
        kol = Kol(name="Creator", email="creator@example.com")
        operator = Operator(name="Ops", email="ops@example.com")
        self.db.add_all([kol, operator])
        self.db.flush()

        self.db.add(KolEmail(
            kol_id=kol.id, email="creator@example.com",
            email_normalized="creator@example.com", is_primary=True,
        ))
        self.db.add(ProjectAssessment(project_code="dola_uk", kol_id=kol.id))

        thread = Thread(kol_id=kol.id, subject="Collaboration")
        self.db.add(thread)
        self.db.flush()

        message = Message(
            thread_id=thread.id, direction="inbound",
            from_email="creator@example.com", to_email="team@example.com",
            subject="Re: Collaboration", body_text="USD 500 per video",
            message_id="provider-1", received_at=now,
        )
        self.db.add(message)
        self.db.flush()

        self.db.add(ScheduledReply(
            thread_id=thread.id, source_message_id=message.id, status="queued",
        ))
        self.db.add(FeishuSyncTask(
            kol_id=kol.id, source_message_id=message.id, next_retry_at=now,
        ))
        self.db.add(Note(thread_id=thread.id, operator_id=operator.id, content="内部备注"))
        self.db.add(SendLog(kol_id=kol.id, thread_id=thread.id, status="sent"))
        self.db.commit()
        return kol.id

    def test_delete_kol_with_thread_and_children(self):
        kol_id = self._seed_kol_with_children()

        resp = self.client.delete(f"/api/kols/{kol_id}")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"deleted": kol_id})
        check = self.Session()
        try:
            self.assertIsNone(check.query(Kol).get(kol_id))
            for model in (
                Thread, Message, ScheduledReply, FeishuSyncTask,
                Note, SendLog, KolEmail, ProjectAssessment,
            ):
                self.assertEqual(
                    check.query(model).count(), 0,
                    f"{model.__tablename__} 应随 KOL 删除清空",
                )
            # 无关数据不受影响
            self.assertEqual(check.query(Operator).count(), 1)
        finally:
            check.close()

    def test_delete_kol_without_children(self):
        kol = Kol(name="Lonely", email="lonely@example.com")
        self.db.add(kol)
        self.db.commit()

        resp = self.client.delete(f"/api/kols/{kol.id}")

        self.assertEqual(resp.status_code, 200)
        check = self.Session()
        try:
            self.assertIsNone(check.query(Kol).get(kol.id))
        finally:
            check.close()

    def test_delete_missing_kol_returns_404(self):
        resp = self.client.delete("/api/kols/99999")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
