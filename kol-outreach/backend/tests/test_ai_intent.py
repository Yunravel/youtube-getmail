import os
import unittest
from unittest.mock import MagicMock, patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["OPENAI_API_KEY"] = "test-key"

from services import ai_intent


def _fake_client_returning(json_str: str):
    """构造一个假的 OpenAI client，chat.completions.create 返回指定 JSON。"""
    client = MagicMock()
    msg = MagicMock()
    msg.message.content = json_str
    choice = MagicMock()
    choice.message = msg.message
    client.chat.completions.create.return_value = MagicMock(choices=[choice])
    return client


class AiIntentTest(unittest.TestCase):
    def setUp(self):
        # 默认 client 返回一个合法意向 + 三新字段
        self._orig_get = ai_intent._get_client

    def tearDown(self):
        ai_intent._get_client = self._orig_get

    def test_model_result_has_new_quote_fields(self):
        resp_json = '''{
            "intent": "high",
            "intent_score": 85,
            "budget_mentioned": "$2,000 USD",
            "key_questions": [],
            "timeline": "flexible",
            "summary": "感兴趣并报价",
            "suggested_action": "立即跟进",
            "collaboration_type": "Dedicated Video / Integration",
            "platform_rate": "$2,000 USD",
            "external_rate": null,
            "complete_quote": "$2,000 USD dedicated video",
            "creator_tags": ["AI工具", "软件教程"]
        }'''
        ai_intent._get_client = lambda: _fake_client_returning(resp_json)
        result = ai_intent.analyze_intent("My rate is $2,000 USD for dedicated video.")
        self.assertEqual(result["collaboration_type"], "Dedicated Video / Integration")
        self.assertEqual(result["platform_rate"], "$2,000 USD")
        self.assertIsNone(result["external_rate"])
        self.assertEqual(result["complete_quote"], "$2,000 USD dedicated video")
        self.assertEqual(result["creator_tags"], ["AI工具", "软件教程"])
        self.assertEqual(result["analysis_source"], "model")

    def test_missing_new_fields_get_defaults(self):
        # 模型漏掉新字段时，setdefault 补 None
        resp_json = '''{
            "intent": "medium",
            "intent_score": 50,
            "budget_mentioned": null,
            "key_questions": [],
            "timeline": "none",
            "summary": "模糊兴趣",
            "suggested_action": "温和跟进"
        }'''
        ai_intent._get_client = lambda: _fake_client_returning(resp_json)
        result = ai_intent.analyze_intent("maybe could work")
        self.assertIsNone(result["collaboration_type"])
        self.assertIsNone(result["platform_rate"])
        self.assertIsNone(result["external_rate"])
        self.assertIsNone(result["complete_quote"])
        self.assertEqual(result["creator_tags"], [])

    def test_rule_based_fallback_has_new_fields(self):
        # 无 API key → 走规则兜底，也应带三个字段（None）
        ai_intent._get_client = lambda: None
        result = ai_intent.analyze_intent("not interested, stop emailing")
        self.assertIn(result["collaboration_type"], (None,))
        self.assertIsNone(result["platform_rate"])
        self.assertIsNone(result["external_rate"])
        self.assertIsNone(result["complete_quote"])
        self.assertEqual(result["creator_tags"], [])
        self.assertEqual(result["analysis_source"], "rules")

    def test_external_rate_for_other_platforms(self):
        # 报价表标注其他平台 → external_rate
        resp_json = '''{
            "intent": "high",
            "intent_score": 80,
            "budget_mentioned": null,
            "key_questions": [],
            "timeline": "none",
            "summary": "报价",
            "suggested_action": "立即跟进",
            "collaboration_type": "Dedicated Video / Integration / Bundle",
            "platform_rate": "$2,000 USD",
            "external_rate": "TikTok $1,200; Instagram $900"
        }'''
        ai_intent._get_client = lambda: _fake_client_returning(resp_json)
        result = ai_intent.analyze_intent("YouTube $2000, TikTok $1200")
        self.assertEqual(result["platform_rate"], "$2,000 USD")
        self.assertEqual(result["external_rate"], "TikTok $1,200; Instagram $900")


if __name__ == "__main__":
    unittest.main()
