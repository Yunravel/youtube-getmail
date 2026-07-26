r"""parse_min_quote / detect_quote 报价解析回归测试。

核心保护点：
1. 带 k/K(千) m/M(百万) 单位后缀的报价不得被截断
   （历史 bug："$10k" 被正则字符类 [\d,\s.] 切到 "10" → 误识别为 10）。
2. 引用块（quoted reply / forward）里的金额不得被当成 KOL 报价
   （历史 bug：我方外联邮件里的预算 "USD 800" 被引用回来 → confirmed=True
   → 对明确拒绝的达人自动排队回复）。
"""
import os
import unittest
from unittest.mock import patch

# services.ai_profile 在 import 时不会触碰 DB / LLM，但为防止未来变化引入副作用，
# 显式置空凭据（与 test_mailbox.py 一致）。
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("OPENAI_API_KEY", "")

from services.ai_profile import parse_min_quote
from services.quote_detection import detect_quote, strip_quoted_text


class ParseMinQuoteTest(unittest.TestCase):
    # --- 纯数字与千分位（原行为，向后兼容）---
    def test_plain_usd(self):
        self.assertEqual(parse_min_quote("$1200"), (1200, "$"))
        self.assertEqual(parse_min_quote("$1200-$1400"), (1200, "$"))
        self.assertEqual(parse_min_quote("$1,200"), (1200, "$"))
        self.assertEqual(parse_min_quote("$2,000 for video, $1,700 for carousel"), (1700, "$"))

    def test_plain_gbp(self):
        self.assertEqual(parse_min_quote("£1,500"), (1500, "£"))
        self.assertEqual(parse_min_quote("GBP 2500"), (2500, "£"))

    def test_currency_suffix(self):
        # 货币符号在数字后面
        self.assertEqual(parse_min_quote("1200 USD"), (1200, "$"))

    # --- 核心 bug 回归：k/K 后缀 ---
    def test_k_suffix_usd(self):
        self.assertEqual(parse_min_quote("$10k"), (10000, "$"))
        self.assertEqual(parse_min_quote("$5k"), (5000, "$"))
        self.assertEqual(parse_min_quote("$2.5k"), (2500, "$"))

    def test_k_suffix_uppercase(self):
        self.assertEqual(parse_min_quote("$10K"), (10000, "$"))
        self.assertEqual(parse_min_quote("£3K"), (3000, "£"))

    # --- m/M 后缀 ---
    def test_m_suffix(self):
        self.assertEqual(parse_min_quote("$1.2m"), (1_200_000, "$"))
        self.assertEqual(parse_min_quote("$2M"), (2_000_000, "$"))

    # --- 多金额取最小（保留原语义）---
    def test_min_of_multiple(self):
        self.assertEqual(
            parse_min_quote("$5k for reel, $3k for story"),
            (3000, "$"),
        )

    # --- 无报价 / 空值 ---
    def test_no_quote(self):
        self.assertEqual(parse_min_quote("rate card attached"), (None, None))
        self.assertEqual(parse_min_quote(""), (None, None))
        self.assertEqual(parse_min_quote(None), (None, None))

    # --- 过小值仍被过滤（保留原语义，避免序号/百分比误匹配）---
    def test_filter_small_values(self):
        # "see section 3" 里的 3 不应被当成报价
        self.assertEqual(parse_min_quote("see section 3 for details"), (None, None))


# 实测事故样本：明确拒绝 + 引用块里我方预算 USD 800（修复前 confirmed=True）。
GMAIL_PLAIN_REJECTION = (
    "Thanks for reaching out, but I'm not interested at this time.\n"
    "\n"
    "On Mon, Jul 20, 2026 at 10:00 AM Partnership Team <us@example.com> wrote:\n"
    "> Hi, we have a budget of USD 800 for a dedicated video.\n"
    "> Let us know if that works.\n"
)


class StripQuotedTextTest(unittest.TestCase):
    """strip_quoted_text 剥离规则的单元测试（纯函数层）。"""

    def test_plain_body_unchanged(self):
        # 无引用标记的正文原样保留（含 "wrote" 出现在行中间的情况）
        body = "My rate is USD 900 per video.\nI never wrote back until now, sorry."
        self.assertEqual(strip_quoted_text(body), body)

    def test_empty_and_pure_quote(self):
        self.assertEqual(strip_quoted_text(""), "")
        self.assertEqual(strip_quoted_text(None or ""), "")
        # 只有 ">" 行（含嵌套 ">>"）→ 全部剥掉 → 空串
        self.assertEqual(strip_quoted_text("> a\n>> b\n> c"), "")

    def test_gmail_intro_wrapped_across_two_lines(self):
        # Gmail 引导行因换行被拆成两行：首行以 On 开头、次行以 wrote: 结尾
        body = (
            "No thanks.\n"
            "\n"
            "On Mon, Jul 20, 2026 at 10:00 AM Partnership Team\n"
            "<us@example.com> wrote:\n"
            "> We have a budget of USD 800.\n"
        )
        self.assertEqual(strip_quoted_text(body), "No thanks.")

    def test_outlook_from_sent_pair_without_dashes(self):
        # 无 -----Original Message----- 分隔线时，靠 From:+Sent: 相邻两行截断
        body = (
            "Not interested, thanks.\n"
            "\n"
            "From: Partnership Team <us@example.com>\n"
            "Sent: Monday, July 20, 2026 10:00 AM\n"
            "Subject: Budget\n"
            "\n"
            "USD 800 available.\n"
        )
        self.assertEqual(strip_quoted_text(body), "Not interested, thanks.")

    def test_from_without_sent_is_kept(self):
        # 防误杀：正文里普通 "From:" 开头的句子（后面没有 Sent:/Date:）不截断
        body = "From: my perspective, USD 900 per video is fair.\nThat includes one revision."
        self.assertEqual(strip_quoted_text(body), body)

    def test_french_gmail_intro(self):
        body = (
            "Merci, mais non merci.\n"
            "\n"
            "Le lun. 20 juil. 2026 à 10:00, Partnership Team <us@example.com> a écrit :\n"
            "Nous avons un budget de USD 800 pour une vidéo.\n"
        )
        self.assertEqual(strip_quoted_text(body), "Merci, mais non merci.")

    def test_signature_delimiter_with_trailing_space(self):
        # RFC 3676 签名分隔符 "-- "（带尾随空格的变体）
        body = "Deal, USD 1,200 works.\n-- \nJane\nGet my presets for $99!"
        self.assertEqual(strip_quoted_text(body), "Deal, USD 1,200 works.")


class DetectQuoteQuotedBodyTest(unittest.TestCase):
    """detect_quote 引用块误报回归：引用/转发内容里的金额不得触发 confirmed。"""

    def _amounts(self, result):
        return [(item["currency"], item["amount"]) for item in result["items"]]

    def test_rejection_with_quoted_budget_not_confirmed(self):
        # 1) 实测复现样本 → 不得 confirmed（修复前会对拒绝邮件自动排队回复）
        result = detect_quote(GMAIL_PLAIN_REJECTION)
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["items"], [])
        self.assertTrue(result["body_quoted_stripped"])

    def test_real_quote_above_quoted_budget(self):
        # 2) 最重要正例保护：上半正文有 KOL 真报价，引用块里有我方预算
        #    → confirmed=True 且 items 只含 KOL 的金额，剥离不能吞掉真报价
        body = (
            "Hi team, my rate for a dedicated video is USD 900.\n"
            "\n"
            "On Mon, Jul 20, 2026 at 10:00 AM Partnership Team <us@example.com> wrote:\n"
            "> Hi, we have a budget of USD 800 for a dedicated video.\n"
            "> Let us know if that works.\n"
        )
        result = detect_quote(body)
        self.assertTrue(result["confirmed"])
        self.assertEqual(self._amounts(result), [("USD", 900)])

    def test_angle_prefixed_lines_without_intro(self):
        # 3) 无引导行、仅 ">" 前缀行里的金额 → 不计入
        result = detect_quote("Sure, sounds interesting!\n> Our budget is USD 800 per video.")
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["items"], [])

    def test_outlook_original_message_divider(self):
        # 4) -----Original Message----- 之后的金额 → 不计入
        body = (
            "Sounds good, let me think it over.\n"
            "\n"
            "-----Original Message-----\n"
            "From: Partnership Team <us@example.com>\n"
            "Sent: Monday, July 20, 2026 10:00 AM\n"
            "To: Jane Creator\n"
            "Subject: Collaboration\n"
            "\n"
            "We have a budget of USD 800 for a dedicated video, 50% upfront.\n"
        )
        result = detect_quote(body)
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["items"], [])

    def test_chinese_zaixiedao_intro(self):
        # 5a) 中文 Gmail "在 ... 写道：" 之后的金额 → 不计入
        body = (
            "你好，这次先不合作了，谢谢理解。\n"
            "\n"
            "在 2026年7月20日 周一 上午10:00，Partnership Team <us@example.com> 写道：\n"
            "我们有 USD 800 的预算用于一条定制视频。\n"
        )
        result = detect_quote(body)
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["items"], [])

    def test_chinese_fajianren_header(self):
        # 5b) 中文客户端 "发件人：" 引用头之后的金额 → 不计入
        body = (
            "预算和我的报价差距有点大，先不考虑了。\n"
            "\n"
            "发件人: Partnership Team <us@example.com>\n"
            "发送时间: 2026年7月20日 10:00\n"
            "主题: 合作邀约\n"
            "\n"
            "我们有 USD 800 的预算。\n"
        )
        result = detect_quote(body)
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["items"], [])

    def test_html_converted_gmail_without_angle_prefix(self):
        # 6) HTML 转纯文本后的 Gmail 形态：引导行在、">" 前缀没了
        #    → 引用金额不计入，正文真报价仍计入
        body = (
            "I charge USD 900 for a dedicated video.\n"
            "\n"
            "On Mon, Jul 20, 2026 at 10:00 AM Partnership Team <us@example.com> wrote:\n"
            "Hi, we have a budget of USD 800 for a dedicated video.\n"
            "Let us know if that works.\n"
        )
        result = detect_quote(body)
        self.assertTrue(result["confirmed"])
        self.assertEqual(self._amounts(result), [("USD", 900)])

    def test_pure_quote_mail_no_false_awaiting(self):
        # 7) 整封全是引用（引用里还带 rate card attached / upfront 等诱导词）
        #    → confirmed=False，awaiting_attachment 不误触发，payment_terms 不泄漏
        body = (
            "On Mon, Jul 20, 2026 at 10:00 AM Partnership Team <us@example.com> wrote:\n"
            "> Our rate card is attached, budget USD 800, 50% upfront payment.\n"
            "> Let us know.\n"
        )
        result = detect_quote(body)
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["items"], [])
        self.assertFalse(result["awaiting_attachment"])
        self.assertEqual(result["payment_terms"], [])

    def test_signature_promo_price_ignored(self):
        # 8) 签名分隔符 "--" 之后的促销价不计入
        body = (
            "My rate is USD 1,000 per dedicated video.\n"
            "\n"
            "--\n"
            "Jane Creator\n"
            "Promo: get my preset pack for $99 at shop.example.com\n"
        )
        result = detect_quote(body)
        self.assertTrue(result["confirmed"])
        self.assertEqual(self._amounts(result), [("USD", 1000)])

    def test_attachment_text_is_never_stripped(self):
        # 9) 附件文本不做引用剥离：即使附件里出现 "wrote:" 样式行，
        #    其后金额仍计入（附件报价单是主要合法报价来源）
        attachment_text = (
            "Rate card 2026\n"
            "Dedicated video USD 1,500\n"
            "On Mon, Jul 20, 2026 at 10:00 AM someone wrote:\n"
            "Story package USD 600\n"
        )
        with patch(
            "services.quote_detection.extract_attachment_text",
            return_value=(attachment_text, None),
        ):
            result = detect_quote("Please see attached rate card.", [{"name": "rates.pdf"}])
        self.assertTrue(result["confirmed"])
        self.assertEqual(
            {(item["currency"], item["amount"]) for item in result["items"]},
            {("USD", 1500), ("USD", 600)},
        )
        self.assertEqual(result["sources"], ["attachment:rates.pdf"])

    def test_no_quote_body_flag_false(self):
        # 向后兼容：无引用内容时 body_quoted_stripped=False，识别行为不变
        result = detect_quote("Dedicated YouTube video: USD 1,200\nNo hidden fees.")
        self.assertTrue(result["confirmed"])
        self.assertEqual(self._amounts(result), [("USD", 1200)])
        self.assertFalse(result["body_quoted_stripped"])


if __name__ == "__main__":
    unittest.main()
