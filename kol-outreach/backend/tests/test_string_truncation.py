"""db.SessionLocal 的 before_flush 字符串截断防线(db._truncate_oversized_strings)。

SQLite 开发库不检查 VARCHAR 长度,超长值静默入库;生产 PostgreSQL 会抛
StringDataRightTruncation → 500(典型:超长邮件主题的 Snov webhook,被反复重投)。
监听器注册在 sessionmaker 工厂上,这里用 ``SessionLocal(bind=内存引擎)`` 建 session
——经工厂创建的 session 即使 bind 到别的引擎也会触发工厂级监听器,正好用来验证。
"""
import os
import unittest

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["OPENAI_API_KEY"] = ""

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from db import Base, SessionLocal
from models import Kol, Message, Thread


class StringTruncationTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        # 关键:必须经 SessionLocal 工厂创建(监听器挂在工厂上),不要自建 sessionmaker
        self.db = SessionLocal(bind=self.engine)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _seed_thread(self) -> Thread:
        kol = Kol(name="Creator", email="creator@example.com")
        self.db.add(kol)
        self.db.flush()
        thread = Thread(kol_id=kol.id, subject="Collaboration")
        self.db.add(thread)
        self.db.flush()
        return thread

    def test_kol_name_over_limit_truncated_to_200(self):
        kol = Kol(name="x" * 1000, email="long-name@example.com")
        self.db.add(kol)
        with self.assertLogs("db", level="WARNING") as captured:
            self.db.commit()

        self.assertEqual(len(kol.name), 200)
        self.assertEqual(kol.name, "x" * 200)
        # 截断必须留观测痕迹,静默丢数据不可接受
        self.assertTrue(
            any("kol" in line and "name" in line for line in captured.output),
            f"应有 kol.name 截断告警,实际: {captured.output}",
        )

    def test_message_subject_over_limit_truncated_to_500(self):
        thread = self._seed_thread()
        message = Message(
            thread_id=thread.id, direction="inbound",
            from_email="creator@example.com", to_email="team@example.com",
            subject="s" * 2000,
        )
        self.db.add(message)
        self.db.commit()

        self.assertEqual(len(message.subject), 500)
        self.assertEqual(message.subject, "s" * 500)

    def test_text_column_not_truncated(self):
        # Text 是 String 子类但 length 为 None,天然不在截断范围
        thread = self._seed_thread()
        body = "b" * 10000
        message = Message(
            thread_id=thread.id, direction="inbound",
            from_email="creator@example.com", to_email="team@example.com",
            subject="normal subject", body_text=body,
        )
        self.db.add(message)
        self.db.commit()

        self.assertEqual(message.body_text, body)
        self.assertEqual(len(message.body_text), 10000)

    def test_normal_length_values_untouched(self):
        name = "李雷 Anna-Marie O'Neil"          # 普通长度
        boundary = "e" * 300                      # full_name 是 String(300),恰好等于限长也不能动
        kol = Kol(name=name, email="normal@example.com", full_name=boundary)
        self.db.add(kol)
        self.db.commit()

        self.assertEqual(kol.name, name)
        self.assertEqual(kol.full_name, boundary)
        self.assertEqual(len(kol.full_name), 300)
        self.assertEqual(kol.email, "normal@example.com")

    def test_update_path_also_truncated(self):
        kol = Kol(name="Short", email="update@example.com")
        self.db.add(kol)
        self.db.commit()

        kol.name = "y" * 999
        self.db.commit()

        self.assertEqual(len(kol.name), 200)
        self.assertEqual(kol.name, "y" * 200)
        # 重新查库确认落盘的就是截断后的值
        fresh = self.db.get(Kol, kol.id)
        self.db.refresh(fresh)
        self.assertEqual(fresh.name, "y" * 200)


if __name__ == "__main__":
    unittest.main()
