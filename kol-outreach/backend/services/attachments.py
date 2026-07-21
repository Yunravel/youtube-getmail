"""Normalize optional attachment metadata from external email payloads."""
import re
from typing import Any
from urllib.parse import urlparse


def normalize_attachments(value: Any) -> list[dict]:
    """Return a stable metadata-only attachment list.

    Snov's documented campaign reply APIs do not promise attachment data, but
    webhook relays and future API versions may include it under different keys.
    Unknown fields are intentionally discarded to avoid persisting secrets or
    large inline/base64 file bodies in the database.
    """
    if value in (None, "", []):
        return []

    if isinstance(value, dict):
        nested = value.get("data") or value.get("items") or value.get("files")
        items = nested if isinstance(nested, list) else [value]
    elif isinstance(value, list):
        items = value
    else:
        items = [value]

    normalized: list[dict] = []
    for item in items:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict):
            continue

        name = _text(
            item.get("name")
            or item.get("filename")
            or item.get("file_name")
            or item.get("title")
        )
        url = _safe_url(_text(
            item.get("url")
            or item.get("download_url")
            or item.get("downloadUrl")
            or item.get("link")
        ))
        attachment_id = _text(
            item.get("id") or item.get("attachment_id") or item.get("file_id")
        )
        content_type = _text(
            item.get("content_type")
            or item.get("contentType")
            or item.get("mime_type")
            or item.get("mimeType")
            or item.get("type")
        )
        size = _size(item.get("size") or item.get("file_size") or item.get("fileSize"))

        if not any((name, url, attachment_id)):
            continue
        normalized.append(
            {
                "id": attachment_id or None,
                "name": name or "attachment",
                "url": url or None,
                "size": size,
                "content_type": content_type or None,
            }
        )
    return normalized


def extract_attachments(*containers: Any) -> list[dict]:
    """Find attachment-like fields in one or more payload dictionaries."""
    output: list[dict] = []
    seen: set[tuple] = set()
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in ("attachments", "attachment", "files", "file_attachments"):
            for item in normalize_attachments(container.get(key)):
                identity = (item.get("id"), item.get("url"), item.get("name"), item.get("size"))
                if identity not in seen:
                    output.append(item)
                    seen.add(identity)
    return output


def _text(value: Any) -> str:
    return str(value or "").strip()


def _size(value: Any) -> int | None:
    try:
        size = int(value)
        return size if size >= 0 else None
    except (TypeError, ValueError):
        return None


def _safe_url(value: str) -> str:
    if not value:
        return ""
    parsed = urlparse(value)
    return value if parsed.scheme.lower() in {"http", "https"} and parsed.netloc else ""


# ===== 正文网盘链接提取 =====
# KOL 常把"附件"以网盘分享链接的形式塞在邮件正文里（Snov webhook 不传结构化
# 附件字段）。这里白名单匹配主流网盘域名，从 body_text/raw_body 里抽出这些
# URL 作为附件元数据，让前端附件区能直接渲染可点击链接。
_URL_TOKEN_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)

_FILE_HOST_RE = re.compile(
    r"(?:^|\.)(?:"
    r"drive\.google\.com|"
    r"docs\.google\.com|"
    r"dropbox\.com|"
    r"dl\.dropboxusercontent\.com|"
    r"wetransfer\.com|"
    r"we\.tl|"
    r"1drv\.ms|"
    r"onedrive\.live\.com|"
    r"sharepoint\.com"
    r")$",
    re.IGNORECASE,
)

# host → 展示名（前端附件区显示的附件名）
_HOST_LABEL = {
    "drive.google.com": "Google Drive",
    "docs.google.com": "Google Docs",
    "dropbox.com": "Dropbox",
    "dl.dropboxusercontent.com": "Dropbox",
    "wetransfer.com": "WeTransfer",
    "we.tl": "WeTransfer",
    "1drv.ms": "OneDrive",
    "onedrive.live.com": "OneDrive",
    "sharepoint.com": "SharePoint",
}


def _host_label(host: str) -> str:
    """网盘域名 → 可读展示名；未登记的返回原域名。"""
    host = host.lower().strip(".")
    for i in range(host.count(".") + 1):
        candidate = host.split(".", i)[-1]
        if candidate in _HOST_LABEL:
            return _HOST_LABEL[candidate]
    return host


def extract_links_from_text(text: Any) -> list[dict]:
    """从邮件正文里提取网盘分享链接，返回与附件同构的元数据列表。

    白名单匹配主流网盘域名（Google Drive / Dropbox / WeTransfer / OneDrive /
    SharePoint 等），避免把签名、退订、社媒等无关链接误判成附件。返回项与
    ``normalize_attachments`` 同构，可直接喂给 ``merge_attachments``。
    """
    if not text:
        return []
    output: list[dict] = []
    seen: set[str] = set()
    for match in _URL_TOKEN_RE.findall(str(text)):
        url = _safe_url(match.rstrip(".,);]"))
        if not url:
            continue
        host = urlparse(url).netloc.lower()
        if not _FILE_HOST_RE.search(host):
            continue
        if url in seen:
            continue
        seen.add(url)
        output.append(
            {
                "id": None,
                "name": _host_label(host),
                "url": url,
                "size": None,
                "content_type": None,
            }
        )
    return output


def merge_attachments(*lists: list[dict]) -> list[dict]:
    """合并多个附件列表，按 (id, url, name, size) 去重，保持传入顺序。

    与 ``extract_attachments`` 内部去重逻辑一致；抽成独立函数是为了让结构化
    附件和正文链接可以叠加，而不改动现有 ``extract_attachments`` 的行为。
    """
    output: list[dict] = []
    seen: set[tuple] = set()
    for item_list in lists:
        if not isinstance(item_list, list):
            continue
        for item in item_list:
            if not isinstance(item, dict):
                continue
            identity = (
                item.get("id"),
                item.get("url"),
                item.get("name"),
                item.get("size"),
            )
            if identity in seen:
                continue
            seen.add(identity)
            output.append(item)
    return output
