import csv
import logging
import tempfile
import threading
import unittest
from pathlib import Path

from youtube_collector.collector import CSV_FIELDS, CollectOptions, YouTubeCollector


class FakeApi:
    def __init__(self):
        self.search_calls = []

    def get_regions(self, language):
        if language == "zh_CN":
            return {"US": "美国", "GB": "英国"}
        return {"US": "United States", "GB": "United Kingdom"}

    def search_videos(self, keyword, token=None):
        self.search_calls.append((keyword, token))
        if token is None:
            return {
                "items": [
                    {"id": {"videoId": "v1"}},
                    {"id": {"videoId": "v2"}},
                    {"id": {"videoId": "v3"}},
                ],
                "nextPageToken": "page-2",
            }
        return {"items": [{"id": {"videoId": "v4"}}]}

    def get_videos(self, ids):
        channels = {"v1": "c1", "v2": "c1", "v3": "c2", "v4": "c3"}
        return {
            video_id: {
                "id": video_id,
                "snippet": {"channelId": channels[video_id], "title": f"Video {video_id}"},
                "statistics": {"viewCount": "123"},
            }
            for video_id in ids
        }

    def get_channels(self, ids):
        data = {
            "c1": {
                "id": "c1",
                "snippet": {
                    "title": "Creator One",
                    "description": "Business: hello@example.com https://instagram.com/creator",
                    "customUrl": "@creator-one",
                    "country": "US",
                    "publishedAt": "2020-01-01T00:00:00Z",
                },
                "statistics": {"subscriberCount": "50000", "videoCount": "10", "viewCount": "9999"},
            },
            "c2": {
                "id": "c2",
                "snippet": {"title": "Wrong country", "country": "GB"},
                "statistics": {"subscriberCount": "50000"},
            },
            "c3": {
                "id": "c3",
                "snippet": {"title": "Too small", "country": "US"},
                "statistics": {"subscriberCount": "500"},
            },
        }
        return {channel_id: data[channel_id] for channel_id in ids}


class CollectorTests(unittest.TestCase):
    def test_end_to_end_writes_filtered_deduplicated_csv(self):
        api = FakeApi()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.csv"
            options = CollectOptions(
                ["beauty"], {"美国"}, 10000, 100000, 2, output, email_only=True
            )
            count = YouTubeCollector(api, logging.getLogger("test")).run(
                options, threading.Event()
            )

            self.assertEqual(count, 1)
            self.assertEqual(api.search_calls, [("beauty", None), ("beauty", "page-2")])
            with output.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(list(rows[0]), CSV_FIELDS)
            self.assertEqual(len(CSV_FIELDS), 23)
            self.assertEqual(rows[0]["博主名称"], "Creator One")
            self.assertEqual(rows[0]["联系详情"], "hello@example.com")
            self.assertEqual(rows[0]["Instagram链接"], "https://instagram.com/creator")

    def test_stop_event_avoids_network_search(self):
        api = FakeApi()
        event = threading.Event()
        event.set()
        with tempfile.TemporaryDirectory() as directory:
            options = CollectOptions(["x"], set(), 0, 0, 1, Path(directory) / "stopped.csv")
            count = YouTubeCollector(api, logging.getLogger("test-stop")).run(options, event)
        self.assertEqual(count, 0)
        self.assertEqual(api.search_calls, [])


if __name__ == "__main__":
    unittest.main()
