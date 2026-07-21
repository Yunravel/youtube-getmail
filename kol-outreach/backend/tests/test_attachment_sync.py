"""IMAP 附件同步的单测：覆盖纯函数（文件名安全化、subject 归一、邮件匹配、加密）。

imaplib 网络层不在这里测（需要真实 Gmail 账号），由端到端验证覆盖。
"""
import os
import tempfile
import unittest
from datetime import datetime, timedelta

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("OPENAI_API_KEY", "")

from services.attachment_sync import (  # noqa: E402
    _find_matching_message,
    _normalize_subject,
    _safe_filename,
)
from services.crypto import decrypt_password, encrypt_password  # noqa: E402
from db import SessionLocal, init_db  # noqa: E402
from models import Message, Thread, Kol  # noqa: E402


class TestSafeFilename(unittest.TestCase):
    """_safe_filename：防目录穿越 + 清理控制字符。"""

    def test_strips_path_separators(self):
        # 反斜杠和正斜杠都换成下划线，防 ../../etc/passwd
        self.assertNotIn("/", _safe_filename("../../etc/passwd"))
        self.assertNotIn("\\", _safe_filename("..\\..\\secret"))

    def test_strips_control_and_newline(self):
        # MIME 折叠空白会带 \n，必须清掉（真实 Daniel 邮件就遇到这情况）
        name = "Brendan Jowett - Media Kit\n 2026.pdf"
        result = _safe_filename(name)
        self.assertNotIn("\n", result)
        self.assertIn("Brendan", result)
        self.assertIn(".pdf", result)

    def test_empty_returns_fallback(self):
        self.assertEqual(_safe_filename(""), "attachment")
        self.assertEqual(_safe_filename(None), "attachment")

    def test_long_name_truncated(self):
        long_name = "x" * 300 + ".pdf"
        result = _safe_filename(long_name)
        self.assertLessEqual(len(result), 180)

    def test_preserves_chinese(self):
        self.assertIn("报价单", _safe_filename("KOL 报价单 2026.pdf"))


class TestNormalizeSubject(unittest.TestCase):
    """_normalize_subject：去掉 Re:/Fwd: 前缀。"""

    def test_strips_re_fwd(self):
        self.assertEqual(_normalize_subject("Re: hi"), "hi")
        self.assertEqual(_normalize_subject("Fwd: hi"), "hi")
        self.assertEqual(_normalize_subject("RE: FW: Re: hi"), "hi")
        self.assertEqual(_normalize_subject("Re:Re:Re: hi"), "hi")

    def test_preserves_content(self):
        # 真实 Snov 回信 subject
        s = "Re: ✨ Paid Collaboration Opportunity with Pippit"
        self.assertEqual(_normalize_subject(s), "✨ Paid Collaboration Opportunity with Pippit")

    def test_empty(self):
        self.assertEqual(_normalize_subject(""), "")
        self.assertEqual(_normalize_subject(None), "")


class TestFindMatchingMessage(unittest.TestCase):
    """_find_matching_message：邮件→中台 message 匹配。"""

    @classmethod
    def setUpClass(cls):
        init_db()
        cls.db = SessionLocal()
        # 建一个 KOL + Thread + 两封 inbound message
        kol = Kol(name="Daniel", email="partnership@daniel-dan.biz", status="sent")
        cls.db.add(kol)
        cls.db.flush()
        thread = Thread(kol_id=kol.id, subject="Paid Collab", status="open")
        cls.db.add(thread)
        cls.db.flush()
        cls.message_id = thread.id  # 备用
        # 一封与 IMAP 邮件能匹配上的 message
        cls.msg = Message(
            thread_id=thread.id,
            direction="inbound",
            from_email="partnership@daniel-dan.biz",
            to_email="x@gmail.com",
            subject="Re: ✨ Paid Collaboration Opportunity",
            body_text="hi",
            received_at=datetime(2026, 7, 14, 15, 22, 51),
        )
        cls.db.add(cls.msg)
        cls.db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_match_by_email_and_subject(self):
        # IMAP 那封：from 相同、subject 去 Re: 后一致、时间在窗口内
        m = _find_matching_message(
            self.db,
            from_email="partnership@daniel-dan.biz",
            subject="Re: ✨ Paid Collaboration Opportunity",
            received_at=datetime(2026, 7, 14, 16, 0, 0),
        )
        self.assertIsNotNone(m)
        self.assertEqual(m.id, self.msg.id)

    def test_no_match_unknown_email(self):
        m = _find_matching_message(
            self.db,
            from_email="unknown@nowhere.com",
            subject="Re: hello",
            received_at=datetime(2026, 7, 14),
        )
        self.assertIsNone(m)

    def test_time_window_tolerance(self):
        # ±3 天内能匹配
        m = _find_matching_message(
            self.db,
            from_email="partnership@daniel-dan.biz",
            subject="Re: ✨ Paid Collaboration Opportunity",
            received_at=datetime(2026, 7, 16, 0, 0, 0),  # 2 天后
        )
        self.assertIsNotNone(m)
        # 超出窗口（>3 天）匹配不到
        m_far = _find_matching_message(
            self.db,
            from_email="partnership@daniel-dan.biz",
            subject="Re: ✨ Paid Collaboration Opportunity",
            received_at=datetime(2026, 8, 1),  # 半个月后
        )
        self.assertIsNone(m_far)

    def test_subject_loose_match(self):
        # subject 一方包含另一方也算匹配
        m = _find_matching_message(
            self.db,
            from_email="partnership@daniel-dan.biz",
            subject="Re: ✨ Paid Collaboration Opportunity with Extra Words",
            received_at=datetime(2026, 7, 14),
        )
        self.assertIsNotNone(m)


class TestCrypto(unittest.TestCase):
    """crypto：加密 round-trip。注意本测试环境无 ATTACHMENT_MASTER_KEY，走降级模式。"""

    def test_roundtrip(self):
        plain = "my-secret-app-password-1234"
        token = encrypt_password(plain)
        self.assertNotEqual(token, plain)  # 不能明文存储
        self.assertEqual(decrypt_password(token), plain)

    def test_empty(self):
        self.assertEqual(encrypt_password(""), "")
        self.assertEqual(decrypt_password(""), "")

    def test_decrypt_garbage_returns_empty(self):
        self.assertEqual(decrypt_password("f:invalid-token"), "")


class TestImapClientHelpers(unittest.TestCase):
    """imap_client 的纯解析函数（不连网）。"""

    def test_parse_address(self):
        from services.imap_client import ImapMailbox
        name, email_addr = ImapMailbox._parse_address("Daniel <daniel@example.com>")
        self.assertEqual(name, "Daniel")
        self.assertEqual(email_addr, "daniel@example.com")

    def test_parse_address_bare(self):
        from services.imap_client import ImapMailbox
        name, email_addr = ImapMailbox._parse_address("daniel@example.com")
        self.assertEqual(email_addr, "daniel@example.com")

    def test_parse_address_empty(self):
        from services.imap_client import ImapMailbox
        name, email_addr = ImapMailbox._parse_address("")
        self.assertEqual(name, "")
        self.assertEqual(email_addr, "")


if __name__ == "__main__":
    unittest.main()
