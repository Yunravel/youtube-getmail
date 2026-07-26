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
    _apply_mailbox_context,
    _find_matching_message,
    _ingest_unmatched_email,
    _is_noise_email,
    _load_known_outreach_subjects,
    _normalize_subject,
    _safe_filename,
)
from services.crypto import decrypt_password, encrypt_password  # noqa: E402
from services.email_utils import ensure_kol_email, find_kol_by_any_email  # noqa: E402
from db import Base, SessionLocal, engine, init_db  # noqa: E402
from models import KolEmail, Message, Thread, Kol  # noqa: E402
from services.imap_client import FetchedEmail  # noqa: E402


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

    def test_imap_match_repairs_recipient_and_exact_duplicates(self):
        duplicate = Message(
            thread_id=self.msg.thread_id,
            direction="inbound",
            from_email=self.msg.from_email,
            to_email="unknown@snov.local",
            subject=self.msg.subject,
            body_text=self.msg.body_text,
            received_at=self.msg.received_at,
        )
        self.msg.to_email = "unknown@snov.local"
        self.db.add(duplicate)
        self.db.commit()
        fetched = FetchedEmail(
            uid="123",
            message_id="rfc-message@example.com",
            in_reply_to="original@example.com",
            references="<original@example.com>",
            from_email=self.msg.from_email,
            from_name="Daniel",
            to_email="alias@example.com",
            subject=self.msg.subject,
            date=self.msg.received_at,
        )

        repaired = _apply_mailbox_context(
            self.db, self.msg, fetched, "Team@Example.com"
        )
        self.db.commit()

        self.assertEqual(repaired, 2)
        self.assertEqual(self.msg.to_email, "team@example.com")
        self.assertEqual(duplicate.to_email, "team@example.com")
        self.assertEqual(self.msg.rfc_message_id, "rfc-message@example.com")
        self.assertEqual(duplicate.rfc_message_id, "rfc-message@example.com")
        self.db.delete(duplicate)
        self.db.commit()


def _fetched(**overrides) -> FetchedEmail:
    """构造一封默认为"第三方地址真实回信"的 IMAP 邮件，测试按需覆盖字段。"""
    defaults = dict(
        uid="900",
        message_id="third-party-reply@bossmgmtgrp.com",
        in_reply_to="our-outreach@snov.example",
        references="<our-outreach@snov.example>",
        from_email="nina@bossmgmtgrp.com",
        from_name="Nina",
        to_email="cowanhelena588@gmail.com",
        subject="Re: ✨ Paid YouTube Collaboration with Dola",
        date=datetime(2026, 7, 20, 10, 0, 0),
        body_text="Hi, I manage Helena. She is interested — rates attached.",
    )
    defaults.update(overrides)
    return FetchedEmail(**defaults)


class TestIngestUnmatchedEmail(unittest.TestCase):
    """_ingest_unmatched_email：Snov 未回传的第三方地址回信落库。"""

    def setUp(self):
        # 全套测试共享同一个内存库连接，逐例重建 schema 保证隔离。
        Base.metadata.drop_all(bind=engine)
        init_db()
        self.db = SessionLocal()
        kol = Kol(name="Helena", email="prospect@example.com", status="sent")
        self.db.add(kol)
        self.db.flush()
        self.db.add(Thread(
            kol_id=kol.id,
            subject="Re: ✨ Paid YouTube Collaboration with Dola",
            status="open",
        ))
        self.db.commit()
        self.known = _load_known_outreach_subjects(self.db)

    def tearDown(self):
        self.db.close()

    def test_noise_senders_and_subjects_detected(self):
        self.assertTrue(_is_noise_email(_fetched(
            from_email="mailer-daemon@googlemail.com",
            subject="Delivery Status Notification (Failure)",
        )))
        self.assertTrue(_is_noise_email(_fetched(
            from_email="noreply-dmarc-support@google.com",
            subject="Report domain: example.com",
        )))
        self.assertTrue(_is_noise_email(_fetched(
            from_email="no-reply@accounts.google.com",
            subject="Security alert",
        )))
        self.assertFalse(_is_noise_email(_fetched()))

    def test_real_third_party_reply_is_ingested(self):
        message, created_kol_id = _ingest_unmatched_email(
            self.db, _fetched(), "cowanhelena588@gmail.com", self.known
        )
        self.assertIsNotNone(message)
        self.db.commit()
        self.assertEqual(message.direction, "inbound")
        self.assertEqual(message.from_email, "nina@bossmgmtgrp.com")
        self.assertEqual(message.to_email, "cowanhelena588@gmail.com")
        self.assertIn("interested", message.body_text.lower())
        self.assertEqual(message.rfc_message_id, "third-party-reply@bossmgmtgrp.com")
        # Date 头解析成功 → received_at 用邮件真实时间，非推算值。
        self.assertEqual(message.received_at, datetime(2026, 7, 20, 10, 0, 0))
        self.assertFalse(message.received_at_estimated)
        # 引用链没命中库内消息 → 自动为第三方地址建 KOL 并标记在聊，
        # 返回 created_kol_id 供调用方触发画像补全
        kol = self.db.query(Kol).filter(Kol.email == "nina@bossmgmtgrp.com").one()
        self.assertEqual(created_kol_id, kol.id)
        self.assertEqual(kol.status, "in_conversation")
        self.assertEqual(message.thread.kol_id, kol.id)

    def test_reference_chain_attaches_to_original_thread(self):
        # 库里有我们发出的那封外联（webhook 已回传 outbound），第三方代回的
        # References 命中它 → 挂回原 KOL 会话，不新建裸档，代回地址记为别名。
        original = self.db.query(Kol).filter(Kol.email == "prospect@example.com").one()
        thread = self.db.query(Thread).filter(Thread.kol_id == original.id).one()
        self.db.add(Message(
            thread_id=thread.id,
            direction="outbound",
            from_email="cowanhelena588@gmail.com",
            to_email="prospect@example.com",
            subject="✨ Paid YouTube Collaboration with Dola",
            body_text="hi",
            message_id="snov:outbound-1",
            rfc_message_id="our-outreach@snov.example",
            received_at=datetime(2026, 7, 18, 9, 0, 0),
        ))
        self.db.commit()

        message, created_kol_id = _ingest_unmatched_email(
            self.db, _fetched(), "cowanhelena588@gmail.com", self.known
        )
        self.assertIsNotNone(message)
        self.db.commit()
        self.assertIsNone(created_kol_id)
        self.assertEqual(message.thread_id, thread.id)
        self.assertEqual(message.thread.kol_id, original.id)
        # 没有为 nina@ 新建 KOL；她的地址成为原 KOL 的非主别名邮箱
        self.assertIsNone(
            self.db.query(Kol).filter(Kol.email == "nina@bossmgmtgrp.com").first()
        )
        alias = self.db.query(KolEmail).filter(
            KolEmail.kol_id == original.id,
            KolEmail.email_normalized == "nina@bossmgmtgrp.com",
        ).one()
        self.assertFalse(alias.is_primary)
        self.assertEqual(alias.source, "imap_reference_match")
        self.assertEqual(original.status, "in_conversation")

    def test_reference_hit_does_not_steal_other_kols_sender(self):
        # 引用链命中 KOL A 的会话，但发件地址本身就是 KOL B 的主邮箱：
        # 归属跟着发件人（B），不吞并、不改 A 的别名表。
        original = self.db.query(Kol).filter(Kol.email == "prospect@example.com").one()
        thread = self.db.query(Thread).filter(Thread.kol_id == original.id).one()
        self.db.add(Message(
            thread_id=thread.id,
            direction="outbound",
            from_email="cowanhelena588@gmail.com",
            to_email="prospect@example.com",
            subject="✨ Paid YouTube Collaboration with Dola",
            body_text="hi",
            message_id="snov:outbound-2",
            rfc_message_id="our-outreach@snov.example",
            received_at=datetime(2026, 7, 18, 9, 0, 0),
        ))
        sender = Kol(name="Nina Own", email="nina@bossmgmtgrp.com", status="sent")
        self.db.add(sender)
        self.db.commit()

        message, created_kol_id = _ingest_unmatched_email(
            self.db, _fetched(), "cowanhelena588@gmail.com", self.known
        )
        self.assertIsNotNone(message)
        self.db.commit()
        self.assertIsNone(created_kol_id)
        self.assertEqual(message.thread.kol_id, sender.id)
        self.assertNotEqual(message.thread_id, thread.id)

    def test_ingest_without_date_header_flags_estimated(self):
        # Date 头缺失/解析失败（fetched.date=None）→ received_at 回退入库时刻，
        # 必须带 received_at_estimated=True，否则旧信会绕过自动回复新鲜度闸门。
        before = datetime.utcnow() - timedelta(seconds=5)
        message, _ = _ingest_unmatched_email(
            self.db,
            _fetched(date=None, message_id="no-date-header@bossmgmtgrp.com"),
            "cowanhelena588@gmail.com",
            self.known,
        )
        self.assertIsNotNone(message)
        self.db.commit()
        self.assertTrue(message.received_at_estimated)
        self.assertGreaterEqual(message.received_at, before)
        self.assertLessEqual(
            message.received_at, datetime.utcnow() + timedelta(seconds=5)
        )

    def test_alias_sender_reuses_existing_kol(self):
        # nina@ 已是原 KOL 的别名（此前代回过一次）：即使引用链没命中，
        # 建档查询也要覆盖 kol_email 别名表，直接归位原 KOL，不再新建裸档。
        original = self.db.query(Kol).filter(Kol.email == "prospect@example.com").one()
        ensure_kol_email(
            self.db, original.id, "nina@bossmgmtgrp.com",
            is_primary=False, source="test",
        )
        self.db.commit()

        message, created_kol_id = _ingest_unmatched_email(
            self.db, _fetched(), "cowanhelena588@gmail.com", self.known
        )
        self.assertIsNotNone(message)
        self.db.commit()
        self.assertIsNone(created_kol_id)
        self.assertEqual(message.thread.kol_id, original.id)
        # 没有出现以别名地址为主邮箱的新档
        self.assertEqual(
            self.db.query(Kol).filter(Kol.email == "nina@bossmgmtgrp.com").count(), 0
        )

    def test_ingest_is_idempotent_by_rfc_message_id(self):
        first, _ = _ingest_unmatched_email(
            self.db, _fetched(), "cowanhelena588@gmail.com", self.known
        )
        self.assertIsNotNone(first)
        self.db.commit()
        second, second_created = _ingest_unmatched_email(
            self.db, _fetched(uid="901"), "cowanhelena588@gmail.com", self.known
        )
        self.assertIsNone(second)
        self.assertIsNone(second_created)

    def test_noise_email_not_ingested(self):
        message, _ = _ingest_unmatched_email(
            self.db,
            _fetched(
                from_email="mailer-daemon@googlemail.com",
                subject="Delivery Status Notification (Failure)",
            ),
            "cowanhelena588@gmail.com",
            self.known,
        )
        self.assertIsNone(message)

    def test_non_reply_mail_not_ingested(self):
        # 没有 In-Reply-To/References：不是对任何邮件的回复（通知/推广），不落库
        message, _ = _ingest_unmatched_email(
            self.db,
            _fetched(in_reply_to="", references=""),
            "cowanhelena588@gmail.com",
            self.known,
        )
        self.assertIsNone(message)

    def test_unrelated_subject_not_ingested(self):
        message, _ = _ingest_unmatched_email(
            self.db,
            _fetched(subject="Your weekly crypto digest"),
            "cowanhelena588@gmail.com",
            self.known,
        )
        self.assertIsNone(message)


class TestFindKolByAnyEmail(unittest.TestCase):
    """find_kol_by_any_email：主表 kol.email + kol_email 别名表联合查档。"""

    def setUp(self):
        Base.metadata.drop_all(bind=engine)
        init_db()
        self.db = SessionLocal()
        self.kol = Kol(name="Creator", email="primary@example.com", status="sent")
        self.db.add(self.kol)
        self.db.flush()
        ensure_kol_email(
            self.db, self.kol.id, "primary@example.com",
            is_primary=True, source="test",
        )
        ensure_kol_email(
            self.db, self.kol.id, "alias@agency.com",
            is_primary=False, source="test",
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_hits_main_table_email_case_insensitive(self):
        found = find_kol_by_any_email(self.db, "  Primary@Example.COM ")
        self.assertIsNotNone(found)
        self.assertEqual(found.id, self.kol.id)

    def test_hits_alias_email(self):
        found = find_kol_by_any_email(self.db, "alias@agency.com")
        self.assertIsNotNone(found)
        self.assertEqual(found.id, self.kol.id)

    def test_miss_and_blank_return_none(self):
        self.assertIsNone(find_kol_by_any_email(self.db, "nobody@nowhere.com"))
        self.assertIsNone(find_kol_by_any_email(self.db, ""))
        self.assertIsNone(find_kol_by_any_email(self.db, None))


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
