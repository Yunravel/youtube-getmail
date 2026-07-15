import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from youtube_collector.crawler import (
    BrowserCrawler,
    _new_emails,
    classify_email_status,
    country_matches,
    parse_about_rows,
    parse_localized_number,
    requires_email_verification,
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

    def test_detects_login_or_captcha_email_gate(self):
        self.assertTrue(requires_email_verification("需登录才能查看电子邮件地址"))
        self.assertTrue(requires_email_verification("View email address"))
        self.assertTrue(requires_email_verification("Sign in to see email address"))
        self.assertTrue(requires_email_verification("I'm not a robot reCAPTCHA"))
        self.assertFalse(requires_email_verification("Email: public@creator.test"))

    def test_email_status_variants(self):
        self.assertEqual(classify_email_status("public@creator.test", False), "已获取")
        self.assertIn("另有需人工验证", classify_email_status("public@creator.test", True))
        self.assertIn("需人工验证", classify_email_status("", True))
        self.assertEqual(classify_email_status("", False), "未发现")

    def test_persistent_profile_forces_visible_browser_during_login(self):
        with TemporaryDirectory() as directory:
            playwright = Mock()
            context = Mock()
            playwright.chromium.launch_persistent_context.return_value = context
            crawler = BrowserCrawler(
                Mock(), show_browser=False, profile_dir=Path(directory) / "profile"
            )

            self.assertIs(crawler._launch_context(playwright, force_headed=True), context)
            options = playwright.chromium.launch_persistent_context.call_args.kwargs
            self.assertEqual(options["user_data_dir"], str(Path(directory) / "profile"))
            self.assertFalse(options["headless"])
            self.assertEqual(options["channel"], "chrome")

    def test_detects_email_revealed_after_button_click(self):
        before = "更多信息\n查看电子邮件地址\nPublic: hello@example.com"
        after = "更多信息\nPublic: hello@example.com\nBusiness: team@creator.test"
        self.assertEqual(_new_emails(before, after), ["team@creator.test"])


if __name__ == "__main__":
    unittest.main()
