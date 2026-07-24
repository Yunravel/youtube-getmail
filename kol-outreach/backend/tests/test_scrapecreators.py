"""ScrapeCreators 客户端与 pipeline 集成的单元测试。

用 unittest.mock.patch 拦截真实 HTTP，不消耗 API 额度，也不需要真实 key。
匹配 test_crawler_products.py 的风格（stdlib unittest，模块级 env stub）。
"""
import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["OPENAI_API_KEY"] = ""

from services.crawler import scrapecreators
from services.crawler.config_rules import allowedByProduct, countryAliases


class ScrapeCreatorsClientTest(unittest.TestCase):
    """客户端字段抽取与端点映射的纯逻辑测试（不发 HTTP）。"""

    def setUp(self):
        # 用固定 key 构造客户端，避免依赖环境变量。
        self.client = scrapecreators.ScrapeCreatorsClient(
            api_key="test-key", base_url="https://api.test.example"
        )

    def test_is_configured_with_key_and_base_url(self):
        self.assertTrue(self.client.is_configured)

    def test_is_not_configured_without_key(self):
        c = scrapecreators.ScrapeCreatorsClient(api_key="", base_url="https://api.test.example")
        self.assertFalse(c.is_configured)

    def test_extract_username_strips_at_prefix(self):
        self.assertEqual(self.client.extract_username({"uniqueId": "@handle"}, "tiktok"), "handle")
        self.assertEqual(self.client.extract_username({"username": "plain"}, "instagram"), "plain")
        self.assertIsNone(self.client.extract_username({}, "youtube"))

    def test_to_int_handles_abbreviations(self):
        self.assertEqual(self.client._to_int("1.2M"), 1_200_000)
        self.assertEqual(self.client._to_int("5K"), 5_000)
        self.assertEqual(self.client._to_int(1234), 1234)
        self.assertEqual(self.client._to_int("1,234"), 1234)
        self.assertIsNone(self.client._to_int(None))
        self.assertIsNone(self.client._to_int("not-a-number"))

    def test_extract_followers_handles_field_variants(self):
        # 已用真实 API 响应确认字段名（2026-07-23）
        # YouTube 扁平 subscriberCount
        self.assertEqual(self.client.extract_followers({"subscriberCount": 42}, "youtube"), 42)
        # TikTok stats.followerCount（嵌套）
        self.assertEqual(
            self.client.extract_followers({"stats": {"followerCount": 1000}, "user": {}}, "tiktok"), 1000
        )
        # Instagram data.user.edge_followed_by.count（深层嵌套）
        ig = {"data": {"user": {"edge_followed_by": {"count": 677795648}}}}
        self.assertEqual(self.client.extract_followers(ig, "instagram"), 677795648)

    def test_extract_email_lowercased(self):
        self.assertEqual(self.client.extract_email({"email": "Hello@Example.com"}, "youtube"), "hello@example.com")
        self.assertIsNone(self.client.extract_email({}, "youtube"))
        # IG 用 business_email
        self.assertEqual(
            self.client.extract_email({"data": {"user": {"business_email": "Biz@Test.com"}}}, "instagram"),
            "biz@test.com",
        )

    def test_extract_tiktok_signature_as_description(self):
        # TikTok 的简介字段是 signature，不是 description
        tt = {"user": {"signature": "comedy creator"}, "stats": {}}
        self.assertEqual(self.client.extract_description(tt, "tiktok"), "comedy creator")

    def test_extract_profile_url_falls_back_to_constructed(self):
        # 没有 channel/url 字段时用 username 拼
        url = self.client.extract_profile_url({"name": "test", "handle": "@test"}, "youtube")
        self.assertEqual(url, "https://www.youtube.com/@test")
        url = self.client.extract_profile_url({"user": {"uniqueId": "test"}, "stats": {}}, "tiktok")
        self.assertEqual(url, "https://www.tiktok.com/@test")

    def test_search_path_per_platform(self):
        self.assertEqual(self.client._search_path("youtube"), "/v1/youtube/search")
        self.assertEqual(self.client._search_path("tiktok"), "/v1/tiktok/search/users")
        self.assertEqual(self.client._search_path("instagram"), "/v1/instagram/search/profiles")
        self.assertEqual(self.client._search_path("unknown"), "")

    def test_get_returns_none_when_not_configured(self):
        c = scrapecreators.ScrapeCreatorsClient(api_key="", base_url="")
        # _get 在未配置时应快速返回 None，不发请求
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(c._get("/v1/test", {"q": "x"}))
        finally:
            loop.close()
        self.assertIsNone(result)

    def test_get_returns_none_on_non_200(self):
        """非 200 响应应返回 None，不抛异常（never-raise 契约）。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        self.client._client = mock_client
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(self.client._get("/v1/test", {"q": "x"}))
        finally:
            loop.close()
        self.assertIsNone(result)


class PipelineIntegrationTest(unittest.TestCase):
    """_discover_via_scrapecreators 的集成测试（mock 掉真实 API 调用）。"""

    def test_returns_empty_when_not_configured(self):
        """API key 未配置时应跳过，返回空列表，不抛异常。"""
        from services.crawler import pipeline
        loop = asyncio.new_event_loop()
        try:
            with patch.object(scrapecreators.ScrapeCreatorsClient, "is_configured",
                              new_callable=lambda: property(lambda self: False)):
                result = loop.run_until_complete(
                    pipeline._discover_via_scrapecreators(["Dola"], [], None)
                )
        finally:
            loop.close()
        self.assertEqual(result, [])

    def test_country_whitelist_filters_non_european(self):
        """_build_scrapecreators_row 应丢弃国家不在产品白名单内的候选。"""
        from services.crawler import pipeline
        from scripts._parse_utils import platform_normalize

        client = scrapecreators.ScrapeCreatorsClient(api_key="k", base_url="https://x")
        # 美国达人 —— 不在 Dola 的欧洲白名单内，应被过滤（YouTube 扁平结构）
        us_profile = {"handle": "@uscreator", "country": "United States", "subscriberCount": 10000}
        row = pipeline._build_scrapecreators_row(
            client, "youtube", "uscreator", us_profile, "Dola", "lifestyle creator tips",
            platform_normalize, allowedByProduct,
        )
        self.assertIsNone(row)

    def test_country_whitelist_keeps_european(self):
        """欧洲达人在白名单内应保留，且 country 正确归一。"""
        from services.crawler import pipeline
        from scripts._parse_utils import platform_normalize

        client = scrapecreators.ScrapeCreatorsClient(api_key="k", base_url="https://x")
        # YouTube 扁平结构，country 字段在顶层
        uk_profile = {
            "handle": "@ukcreator", "country": "UK",
            "subscriberCount": 12000, "email": "hi@ukcreator.com",
            "description": "lifestyle vlogger",
        }
        row = pipeline._build_scrapecreators_row(
            client, "youtube", "ukcreator", uk_profile, "Dola", "lifestyle creator tips",
            platform_normalize, allowedByProduct,
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["platform"], "YouTube")
        self.assertEqual(row["account"], "ukcreator")
        # country 入库时归一为中文规范名（services.country_normalize）。
        self.assertEqual(row["country_region"], "英国")
        self.assertEqual(row["followers"], 12000)
        self.assertEqual(row["contact_email"], "hi@ukcreator.com")
        self.assertEqual(row["discovery_method"], "ScrapeCreators API")

    def test_tiktok_country_inferred_from_description(self):
        """TikTok 无 country 字段，应从 signature 文本推断国家。"""
        from services.crawler import pipeline
        from scripts._parse_utils import platform_normalize

        client = scrapecreators.ScrapeCreatorsClient(api_key="k", base_url="https://x")
        tt_profile = {
            "user": {"uniqueId": "berlincreator", "nickname": "Berlin Creator",
                      "signature": "Based in Berlin, Germany 🇩🇪 lifestyle tips"},
            "stats": {"followerCount": 8000},
        }
        row = pipeline._build_scrapecreators_row(
            client, "tiktok", "berlincreator", tt_profile, "Dola", "lifestyle creator tips",
            platform_normalize, allowedByProduct,
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["platform"], "TikTok")
        self.assertEqual(row["country_region"], "德国")
        self.assertEqual(row["followers"], 8000)


class DolaEuropeConfigTest(unittest.TestCase):
    """Dola 欧洲化配置的回归测试。"""

    def test_dola_allows_more_than_uk(self):
        dola = allowedByProduct["Dola"]
        self.assertIn("United Kingdom", dola)
        self.assertIn("Germany", dola)
        self.assertIn("France", dola)
        self.assertIn("Poland", dola)
        self.assertIn("Estonia", dola)
        self.assertGreater(len(dola), 20)  # 远不止 UK 一个

    def test_dola_country_aliases_fully_covered(self):
        """allowedByProduct['Dola'] 的每个国家都必须能被 countryAliases 正则命中。"""
        alias_countries = {name for name, _ in countryAliases}
        missing = allowedByProduct["Dola"] - alias_countries
        self.assertEqual(missing, set(), f"Dola 国家在 countryAliases 中缺失: {missing}")

    def test_country_regex_matches_demonym(self):
        """验证补全的正则能命中常见的国家标识（demonym / 城市）。"""
        import re
        alias_map = {name: pat for name, pat in countryAliases}
        # Ireland 正则应命中 "irish" / "dublin"
        self.assertTrue(alias_map["Ireland"].search("based in dublin"))
        self.assertTrue(alias_map["Ireland"].search("irish creator"))
        # Poland 应命中 "polish" / "warsaw"
        self.assertTrue(alias_map["Poland"].search("warsaw based"))
        self.assertTrue(alias_map["Czech Republic"].search("prague"))

    def test_dola_product_terms_have_no_uk_prefix(self):
        """Dola 关键词不应再带 UK 前缀（已放宽至整个欧洲）。"""
        from services.crawler.config_rules import productTerms
        uk_terms = [t for t in productTerms["Dola"] if t.startswith("UK ")]
        self.assertEqual(uk_terms, [], f"仍带 UK 前缀的词: {uk_terms}")


if __name__ == "__main__":
    unittest.main()
