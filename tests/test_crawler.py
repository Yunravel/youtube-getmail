import unittest

from youtube_collector.crawler import (
    country_matches,
    parse_about_rows,
    parse_localized_number,
    unwrap_youtube_redirect,
)


class CrawlerParserTests(unittest.TestCase):
    def test_localized_numbers(self):
        self.assertEqual(parse_localized_number("6.1万次观看"), 61000)
        self.assertEqual(parse_localized_number("16.7M subscribers"), 16700000)
        self.assertEqual(parse_localized_number("6,389,237,950 views"), 6389237950)

    def test_about_rows_in_chinese(self):
        result = parse_about_rows(
            [
                "www.youtube.com/@Vogue",
                "美国",
                "2008年6月29日注册",
                "1670万位订阅者",
                "5,719 个视频",
                "6,389,237,950次观看",
            ]
        )
        self.assertEqual(result["country"], "美国")
        self.assertEqual(result["subscribers"], 16700000)
        self.assertEqual(result["video_count"], 5719)
        self.assertEqual(result["view_count"], 6389237950)
        self.assertEqual(result["published_at"], "2008年6月29日注册")

    def test_country_aliases(self):
        self.assertTrue(country_matches("美国", {"US"}))
        self.assertTrue(country_matches("United States", {"美国"}))
        self.assertFalse(country_matches("英国", {"US"}))

    def test_iso_code_uses_complete_country_database(self):
        self.assertTrue(country_matches("Switzerland", {"CH"}))

    def test_unwraps_public_redirect_link(self):
        url = "https://www.youtube.com/redirect?event=channel_description&q=https%3A%2F%2Finstagram.com%2Fdemo"
        self.assertEqual(unwrap_youtube_redirect(url), "https://instagram.com/demo")


if __name__ == "__main__":
    unittest.main()
