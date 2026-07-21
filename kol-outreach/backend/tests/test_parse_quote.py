"""parse_min_quote 报价解析回归测试。

核心保护点：带 k/K(千) m/M(百万) 单位后缀的报价不得被截断
（历史 bug："$10k" 被正则字符类 [\d,\s.] 切到 "10" → 误识别为 10）。
"""
import os
import unittest

# services.ai_profile 在 import 时不会触碰 DB / LLM，但为防止未来变化引入副作用，
# 显式置空凭据（与 test_mailbox.py 一致）。
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("OPENAI_API_KEY", "")

from services.ai_profile import parse_min_quote


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


if __name__ == "__main__":
    unittest.main()
