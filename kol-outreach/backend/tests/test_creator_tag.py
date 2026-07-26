import unittest

from services.creator_tag import (
    ALLOWED_CREATOR_TAGS,
    build_creator_tags,
)


class CreatorTagTest(unittest.TestCase):
    def test_known_niche_platform_and_follower_tier(self):
        self.assertEqual(
            build_creator_tags("科技/AI", "youtube", 130_000, None),
            "科技/AI、YouTube、十万粉",
        )

    def test_ai_free_text_and_product_names_never_pass_through(self):
        for raw in (
            "内容创作者",
            "数字内容",
            "品牌合作达人",
            "AI 工具评测、教程、深度解析、实际用例",
            "Dreamina",
            "Kimi",
            "Pippit",
        ):
            tags = build_creator_tags(raw, "Instagram", 50_000, None)
            self.assertEqual(tags, "其他、Instagram、万粉")
            self.assertNotIn(raw, tags)

    def test_every_output_tag_is_in_the_whitelist(self):
        tags = build_creator_tags("UGC创作", "TikTok", 25_000, "en").split("、")
        self.assertEqual(tags, ["UGC创作", "TikTok", "万粉"])
        self.assertTrue(set(tags).issubset(ALLOWED_CREATOR_TAGS))

    def test_language_is_not_used_as_a_creator_tag(self):
        self.assertEqual(
            build_creator_tags("旅游", "Instagram", None, "en"),
            "旅游、Instagram",
        )


if __name__ == "__main__":
    unittest.main()
