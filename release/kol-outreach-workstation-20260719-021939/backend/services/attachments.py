"""Normalize optional attachment metadata from external email payloads."""
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
