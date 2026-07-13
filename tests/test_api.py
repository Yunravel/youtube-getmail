import unittest

from youtube_collector.api import YouTubeApiClient, YouTubeApiError


class FakeResponse:
    def __init__(self, payload, ok=True, status_code=200):
        self.payload = payload
        self.ok = ok
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append((url, params, timeout))
        return next(self.responses)


class ApiClientTests(unittest.TestCase):
    def test_channel_ids_are_batched_by_fifty(self):
        responses = [
            FakeResponse({"items": [{"id": "c0"}]}),
            FakeResponse({"items": [{"id": "c50"}]}),
        ]
        client = YouTubeApiClient("secret", interval=0)
        client.session = FakeSession(responses)
        result = client.get_channels(f"c{i}" for i in range(51))

        self.assertEqual(set(result), {"c0", "c50"})
        self.assertEqual(len(client.session.calls), 2)
        first_params = client.session.calls[0][1]
        second_params = client.session.calls[1][1]
        self.assertEqual(len(first_params["id"].split(",")), 50)
        self.assertEqual(len(second_params["id"].split(",")), 1)
        self.assertEqual(first_params["key"], "secret")

    def test_regions_use_localized_names(self):
        client = YouTubeApiClient("secret", interval=0)
        client.session = FakeSession(
            [FakeResponse({"items": [{"id": "US", "snippet": {"gl": "US", "name": "美国"}}]})]
        )
        self.assertEqual(client.get_regions("zh_CN"), {"US": "美国"})
        self.assertEqual(client.session.calls[0][1]["hl"], "zh_CN")

    def test_api_errors_have_actionable_message(self):
        client = YouTubeApiClient("secret", interval=0)
        client.session = FakeSession(
            [FakeResponse({"error": {"message": "quota exceeded"}}, ok=False, status_code=403)]
        )
        with self.assertRaisesRegex(YouTubeApiError, "403.*quota exceeded"):
            client.search_videos("test")


if __name__ == "__main__":
    unittest.main()
