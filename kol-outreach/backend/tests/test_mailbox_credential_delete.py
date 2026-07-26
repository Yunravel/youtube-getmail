"""DELETE /api/mailbox-credentials/{id} 外键处理(api/mailbox_credentials.py delete_credential)。

scheduled_reply.mailbox_credential_id 是指向 mailbox_credential.id 的可空外键,
没有 ON DELETE 行为。生产 PostgreSQL 上删除任何被 scheduled_reply 引用过的凭据
(哪怕任务已是 sent/cancelled 终态)会被外键拒绝 → 500。SQLite 默认不查外键所以
开发库看不出来;这里显式 PRAGMA foreign_keys=ON,让内存库按生产语义强制外键,
回归时测试会炸。删除语义:引用行置 NULL 保留审计历史,不删 scheduled_reply。
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

from api import mailbox_credentials as mailbox_credentials_api
from db import Base, get_db
from models import Kol, MailboxCredential, Message, ScheduledReply, Thread


class MailboxCredentialDeleteTest(unittest.TestCase):
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
        app.include_router(mailbox_credentials_api.router, prefix="/api/mailbox-credentials")

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

    def _seed_credential_with_reply(self) -> tuple[int, int]:
        """建一条凭据和一条引用它的终态 scheduled_reply,返回 (cred_id, reply_id)。"""
        now = datetime.utcnow()
        cred = MailboxCredential(email="sender@example.com", encrypted_password="ciphertext")
        kol = Kol(name="Creator", email="creator@example.com")
        self.db.add_all([cred, kol])
        self.db.flush()

        thread = Thread(kol_id=kol.id, subject="Collaboration")
        self.db.add(thread)
        self.db.flush()

        message = Message(
            thread_id=thread.id, direction="inbound",
            from_email="creator@example.com", to_email="sender@example.com",
            subject="Re: Collaboration", body_text="USD 500 per video",
            message_id="provider-1", received_at=now,
        )
        self.db.add(message)
        self.db.flush()

        reply = ScheduledReply(
            thread_id=thread.id, source_message_id=message.id,
            mailbox_credential_id=cred.id, status="sent",
        )
        self.db.add(reply)
        self.db.commit()
        return cred.id, reply.id

    def test_delete_credential_referenced_by_scheduled_reply(self):
        cred_id, reply_id = self._seed_credential_with_reply()

        resp = self.client.delete(f"/api/mailbox-credentials/{cred_id}")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"deleted": cred_id})
        check = self.Session()
        try:
            self.assertIsNone(check.get(MailboxCredential, cred_id))
            reply = check.get(ScheduledReply, reply_id)
            self.assertIsNotNone(reply, "scheduled_reply 行应保留(审计历史)")
            self.assertIsNone(reply.mailbox_credential_id)
            self.assertEqual(reply.status, "sent")
        finally:
            check.close()

    def test_delete_missing_credential_returns_404(self):
        resp = self.client.delete("/api/mailbox-credentials/99999")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
