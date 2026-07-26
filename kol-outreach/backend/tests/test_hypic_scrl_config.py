"""Hypic / SCRL 两个新产品的采集配置完整性测试。

验证：
- productTerms 含 Hypic/SCRL 且词数合理（太少搜不到，太多烧 API）
- allowedByProduct 含 Hypic/SCRL，且白名单国家名都在 countryAliases 里有正则
  （否则简介推断会失配，导致目标市场达人被误杀）
- Australia 已补进 countryAliases（两项目二级市场都含澳）
- make_queries 对 Hypic/SCRL 生成 {term}/{term}+generic/{region}+{term} 三组
- 关键白名单国家（美国/英国/加拿大/澳大利亚）能在 countryAliases 命中
"""
import unittest

from services.crawler.config_rules import (
    allowedByProduct,
    countryAliases,
    make_queries,
    productTerms,
)


class HypicScrlConfigTest(unittest.TestCase):
    def test_products_have_terms(self):
        for product in ("Hypic", "SCRL"):
            self.assertIn(product, productTerms, f"{product} 缺关键词词表")
            terms = productTerms[product]
            self.assertGreaterEqual(len(terms), 15, f"{product} 关键词过少: {len(terms)}")
            self.assertLessEqual(len(terms), 30, f"{product} 关键词过多，会烧 ScrapeCreators: {len(terms)}")
            # 关键词非空、去空白
            for t in terms:
                self.assertTrue(t.strip(), f"{product} 含空关键词: {t!r}")

    def test_products_have_country_whitelist(self):
        for product in ("Hypic", "SCRL"):
            self.assertIn(product, allowedByProduct, f"{product} 缺国家白名单")
            wl = allowedByProduct[product]
            # 核心市场美国必在
            self.assertIn("United States", wl, f"{product} 白名单缺核心市场美国")
            self.assertGreaterEqual(len(wl), 5, f"{product} 白名单过小: {wl}")

    def test_whitelist_countries_have_alias_regex(self):
        """白名单里的每个国家必须在 countryAliases 里有正则，否则简介推断失配。"""
        alias_countries = {country for country, _ in countryAliases}
        for product in ("Hypic", "SCRL"):
            missing = allowedByProduct[product] - alias_countries
            self.assertFalse(
                missing,
                f"{product} 白名单国家缺 countryAliases 正则: {sorted(missing)}",
            )

    def test_australia_alias_exists(self):
        """Australia 必须在 countryAliases（两项目二级市场都含澳，原代码缺失）。"""
        countries = {c for c, _ in countryAliases}
        self.assertIn("Australia", countries)
        # 验证正则能命中典型简介文本
        pattern = next(p for c, p in countryAliases if c == "Australia")
        for text in ["based in Sydney", "Australian creator", "Melbourne 🇦🇺"]:
            self.assertIsNotNone(pattern.search(text), f"Australia 正则未命中: {text}")
        # 不应误命中 Austria
        self.assertIsNone(pattern.search("I live in Vienna, Austria"))

    def test_make_queries_generates_region_variants(self):
        """Hypic/SCRL 应生成 {term} / {term}+generic / {region}+{term} 三组查询。"""
        for product in ("Hypic", "SCRL"):
            queries = make_queries([product])
            term_count = len(productTerms[product])
            self.assertEqual(
                len(queries), term_count * 3,
                f"{product} 查询数应为词数*3，实际 {len(queries)}",
            )
            # 第一组是纯词，第二组带 generic 修饰符，第三组带 region 前缀
            self.assertEqual(queries[0][0], product)
            self.assertEqual(queries[1][1], f"{productTerms[product][0]} tutorial")
            # 第三组应以 regionModifiers 里的某个词开头（如 USA/UK/Europe）
            from services.crawler.config_rules import regionModifiers
            third_prefix = queries[2][1].split()[0]
            self.assertIn(
                third_prefix, regionModifiers,
                f"{product} 第三组查询缺 region 前缀: {queries[2]}",
            )

    def test_core_markets_resolvable(self):
        """核心市场国家（美/英/加/澳）能被 countryAliases 正则命中典型简介。"""
        alias_map = {c: p for c, p in countryAliases}
        cases = {
            "United States": ["Los Angeles based", "New York creator", "USA 🇺🇸"],
            "United Kingdom": ["London, UK", "based in England"],
            "Canada": ["Toronto creator", "Vancouver"],
            "Australia": ["Sydney", "Melbourne based"],
        }
        for country, texts in cases.items():
            self.assertIn(country, alias_map, f"{country} 缺正则")
            for text in texts:
                self.assertIsNotNone(
                    alias_map[country].search(text),
                    f"{country} 正则未命中: {text}",
                )


if __name__ == "__main__":
    unittest.main()
