import unittest

from youtube_collector.email_finder import PublicEmailFinder, is_safe_public_url


class FakeResponse:
    def __init__(self, url, body, content_type="text/html; charset=utf-8", ok=True):
        self.url = url
        self.content = body.encode("utf-8")
        self.encoding = "utf-8"
        self.ok = ok
        self.headers = {"Content-Type": content_type, "Content-Length": str(len(self.content))}


class FakeSession:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, **_kwargs):
        self.calls.append(url)
        return self.pages[url]


class PublicEmailFinderTests(unittest.TestCase):
    def test_scans_home_and_contact_page(self):
        pages = {
            "https://acme.test": FakeResponse(
                "https://acme.test", '<html><a href="/contact">Contact us</a></html>'
            ),
            "https://acme.test/contact": FakeResponse(
                "https://acme.test/contact", '<a href="mailto:sales@acme.test">Email</a>'
            ),
        }
        session = FakeSession(pages)
        finder = PublicEmailFinder(
            session=session,
            safety_check=lambda _url: True,
            interval=0,
        )
        result = finder.find(["https://acme.test"])
        self.assertEqual(result.emails, ["sales@acme.test"])
        self.assertEqual(result.sources["sales@acme.test"], "https://acme.test/contact")
        self.assertEqual(session.calls, ["https://acme.test", "https://acme.test/contact"])

    def test_extracts_bracket_obfuscation(self):
        pages = {
            "https://brand.test": FakeResponse(
                "https://brand.test", "Business: creator [at] brand [dot] test"
            )
        }
        result = PublicEmailFinder(
            session=FakeSession(pages), safety_check=lambda _url: True, interval=0
        ).find(["https://brand.test"])
        self.assertEqual(result.emails, ["creator@brand.test"])

    def test_skips_social_sites(self):
        session = FakeSession({})
        result = PublicEmailFinder(
            session=session, safety_check=lambda _url: True, interval=0
        ).find(["https://instagram.com/demo", "https://x.com/demo"])
        self.assertEqual(result.emails, [])
        self.assertEqual(session.calls, [])

    def test_ignores_emails_inside_scripts(self):
        pages = {
            "https://brand.test": FakeResponse(
                "https://brand.test",
                "<script>const fake='tracking@brand.test'</script><p>No public email</p>",
            )
        }
        result = PublicEmailFinder(
            session=FakeSession(pages), safety_check=lambda _url: True, interval=0
        ).find(["https://brand.test"])
        self.assertEqual(result.emails, [])

    def test_rejects_local_network_targets(self):
        self.assertFalse(is_safe_public_url("http://127.0.0.1/private"))
        self.assertFalse(is_safe_public_url("http://localhost/private"))


if __name__ == "__main__":
    unittest.main()
