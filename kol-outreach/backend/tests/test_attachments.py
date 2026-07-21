"""附件元数据归一化 + 正文网盘链接提取的单测。

沿用 test_snov_sync.py 的 env-guard 模式（sqlite 内存库 + unittest）。
纯函数测试不需要 init_db，但保持一致的 import 顺序便于将来扩展。
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("OPENAI_API_KEY", "")

import unittest  # noqa: E402

from services.attachments import (  # noqa: E402
    extract_attachments,
    extract_links_from_text,
    merge_attachments,
    normalize_attachments,
)


class TestExtractLinksFromText(unittest.TestCase):
    """extract_links_from_text：正文网盘链接提取。"""

    def test_google_drive_link(self):
        text = "Hi, here is my media kit: https://drive.google.com/file/d/1cnKyV/view"
        links = extract_links_from_text(text)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["name"], "Google Drive")
        self.assertTrue(links[0]["url"].startswith("https://drive.google.com/"))
        self.assertIsNone(links[0]["size"])
        self.assertIsNone(links[0]["content_type"])

    def test_dropbox_link(self):
        text = "https://www.dropbox.com/s/abc123/brief.pdf?dl=0 thanks"
        links = extract_links_from_text(text)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["name"], "Dropbox")

    def test_wetransfer_and_onedrive(self):
        text = "Files: https://we.tl/r-ABCDEF and https://1drv.ms/x/s!abc"
        links = extract_links_from_text(text)
        names = {l["name"] for l in links}
        self.assertEqual(names, {"WeTransfer", "OneDrive"})
        self.assertEqual(len(links), 2)

    def test_real_shara_reply_from_log(self):
        """真实日志里那封回信：正文含 Google Drive 链接 + 多个产品站链接。"""
        text = (
            "Here's my rate for a dedicated post: approx $220.\n\n"
            "Attached my media kit - https://drive.google.com/file/d/1cnKyVCsde2m9oXMJOfrGHlek0aDQP2jf/view\n\n"
            "Pippit: https://www.pippit.ai/\n"
            "Dreamina: https://www.imagine.art/\n"
        )
        links = extract_links_from_text(text)
        # 只应抽到 drive 链接，pippit/imagine 不是网盘
        self.assertEqual(len(links), 1)
        self.assertIn("drive.google.com", links[0]["url"])
        self.assertEqual(links[0]["name"], "Google Drive")

    def test_no_file_host_returns_empty(self):
        text = "Check https://www.pippit.ai/ and https://www.imagine.art/ - not file hosts"
        self.assertEqual(extract_links_from_text(text), [])

    def test_empty_or_none(self):
        self.assertEqual(extract_links_from_text(None), [])
        self.assertEqual(extract_links_from_text(""), [])
        self.assertEqual(extract_links_from_text("no links here"), [])

    def test_javascript_url_discarded(self):
        text = "click javascript:alert(1) and https://drive.google.com/file/d/x/view"
        links = extract_links_from_text(text)
        self.assertEqual(len(links), 1)
        self.assertTrue(links[0]["url"].startswith("https://"))

    def test_dedup_same_url(self):
        url = "https://drive.google.com/file/d/abc/view"
        text = f"see {url} ... again {url}"
        links = extract_links_from_text(text)
        self.assertEqual(len(links), 1)

    def test_strips_trailing_punctuation(self):
        text = "See (https://drive.google.com/file/d/abc/view)."
        links = extract_links_from_text(text)
        self.assertEqual(len(links), 1)
        self.assertFalse(links[0]["url"].endswith("."))

    def test_url_in_html_href(self):
        """raw_body 是 HTML 时，href 里的 URL 也能被正则抓到。"""
        text = '<a href="https://dropbox.com/s/xyz/file.pdf">my brief</a>'
        links = extract_links_from_text(text)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["name"], "Dropbox")


class TestMergeAttachments(unittest.TestCase):
    """merge_attachments：结构化附件 + 正文链接合并去重。"""

    def test_structured_first_then_links(self):
        structured = [{"id": "a1", "name": "brief.pdf", "url": "https://x/a.pdf", "size": 100}]
        links = [{"id": None, "name": "Google Drive", "url": "https://drive.google.com/x", "size": None}]
        merged = merge_attachments(structured, links)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["name"], "brief.pdf")  # 结构化在前
        self.assertEqual(merged[1]["name"], "Google Drive")

    def test_dedup_identical(self):
        item = {"id": None, "name": "Google Drive", "url": "https://drive.google.com/x", "size": None}
        merged = merge_attachments([item], [item])
        self.assertEqual(len(merged), 1)

    def test_empty_lists(self):
        self.assertEqual(merge_attachments([], []), [])
        self.assertEqual(merge_attachments([], [{"url": "u", "name": "n"}]), [{"url": "u", "name": "n"}])

    def test_non_list_input_ignored(self):
        merged = merge_attachments(None, [{"url": "u", "name": "n"}])  # type: ignore[arg-type]
        self.assertEqual(len(merged), 1)

    def test_extract_attachments_unchanged(self):
        """回归：原 extract_attachments 行为不变。"""
        payload = {"attachments": [{"filename": "a.pdf", "downloadUrl": "https://x/a.pdf", "size": 10}]}
        out = extract_attachments(payload)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["name"], "a.pdf")
        self.assertEqual(out[0]["size"], 10)

    def test_normalize_attachments_unchanged(self):
        """回归：normalize_attachments 仍丢弃非白名单 scheme。"""
        out = normalize_attachments({"filename": "bad", "url": "javascript:alert(1)"})
        self.assertEqual(len(out), 1)
        self.assertIsNone(out[0]["url"])

    def test_end_to_end_merge_with_extract_attachments(self):
        """模拟 webhook 调用：结构化附件 + 正文链接合并。"""
        payload = {"attachments": [{"filename": "contract.pdf", "downloadUrl": "https://x/c.pdf", "size": 2048}]}
        raw_body = "Signed! Also see my portfolio https://drive.google.com/file/d/zzz/view"
        merged = merge_attachments(
            extract_attachments(payload),
            extract_links_from_text(raw_body),
        )
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["name"], "contract.pdf")
        self.assertEqual(merged[1]["name"], "Google Drive")


if __name__ == "__main__":
    unittest.main()
