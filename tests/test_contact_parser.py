import unittest

from youtube_collector.contact_parser import extract_public_contacts


class ContactParserTests(unittest.TestCase):
    def test_extracts_public_links_and_email(self):
        result = extract_public_contacts(
            "Business: hello@example.com Instagram.com/demo t.me/demo https://x.com/demo"
        )
        self.assertEqual(result["email"], "hello@example.com")
        self.assertEqual(result["instagram"], "https://Instagram.com/demo")
        self.assertEqual(result["telegram"], "https://t.me/demo")
        self.assertEqual(result["twitter"], "https://x.com/demo")

    def test_deduplicates(self):
        result = extract_public_contacts("t.me/demo and t.me/demo")
        self.assertEqual(result["telegram"], "https://t.me/demo")


if __name__ == "__main__":
    unittest.main()

