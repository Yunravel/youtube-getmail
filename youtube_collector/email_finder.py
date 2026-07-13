from __future__ import annotations

import html
import ipaddress
import re
import socket
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urljoin, urlparse

import requests

from .contact_parser import EMAIL_RE


MAX_HTML_BYTES = 2_000_000
CONTACT_MARKERS = (
    "contact", "about", "business", "press", "media", "imprint", "legal",
    "kontakt", "contato", "contacto", "联系", "聯繫", "关于", "關於",
)
SOCIAL_HOSTS = {
    "youtube.com", "www.youtube.com", "instagram.com", "www.instagram.com",
    "facebook.com", "www.facebook.com", "x.com", "www.x.com", "twitter.com",
    "www.twitter.com", "tiktok.com", "www.tiktok.com", "t.me", "wa.me",
}
PLACEHOLDER_DOMAINS = {"example.com", "example.org", "example.net", "domain.com"}
OBFUSCATED_RE = re.compile(
    r"(?i)([\w.+-]+)\s*(?:\[at\]|\(at\))\s*([\w-]+(?:\s*(?:\[dot\]|\(dot\))\s*[\w-]+)+)"
)


@dataclass(frozen=True)
class EmailDiscovery:
    emails: list[str]
    sources: dict[str, str]
    scanned_urls: list[str]


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._anchor_href = ""
        self._anchor_text: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if tag.lower() == "a":
            values = dict(attrs)
            self._anchor_href = values.get("href") or ""
            self._anchor_text = []

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        self.text.append(data)
        if self._anchor_href:
            self._anchor_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1
            return
        if self._ignored_depth:
            return
        if tag.lower() == "a" and self._anchor_href:
            self.links.append((self._anchor_href, " ".join(self._anchor_text).strip()))
            self._anchor_href = ""
            self._anchor_text = []


def is_safe_public_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    hostname = parsed.hostname.casefold()
    if hostname == "localhost" or hostname.endswith(".local"):
        return False
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, parsed.port or 443)}
    except OSError:
        return False
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            return False
    return True


def _extract_emails(text: str) -> list[str]:
    decoded = html.unescape(text or "")
    for match in OBFUSCATED_RE.finditer(decoded):
        domain = re.sub(r"\s*(?:\[dot\]|\(dot\))\s*", ".", match.group(2), flags=re.I)
        decoded += f" {match.group(1)}@{domain}"
    found = []
    for match in EMAIL_RE.finditer(decoded):
        email = match.group(1).rstrip(".").casefold()
        domain = email.rsplit("@", 1)[-1]
        if domain not in PLACEHOLDER_DOMAINS and email not in found:
            found.append(email)
    return found


class PublicEmailFinder:
    """Find emails on a channel's explicitly published websites, with a small crawl budget."""

    def __init__(
        self,
        session: requests.Session | None = None,
        status: Callable[[str], None] | None = None,
        safety_check: Callable[[str], bool] = is_safe_public_url,
        timeout: float = 10,
        interval: float = 0.4,
        max_pages_per_site: int = 3,
    ):
        self.session = session or requests.Session()
        self.status = status or (lambda _message: None)
        self.safety_check = safety_check
        self.timeout = timeout
        self.interval = interval
        self.max_pages_per_site = max_pages_per_site
        self.headers = {
            "User-Agent": "Mozilla/5.0 (compatible; YouTubePublicContactCollector/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        }

    def find(self, urls: list[str]) -> EmailDiscovery:
        emails: list[str] = []
        sources: dict[str, str] = {}
        scanned: list[str] = []
        visited_origins: set[str] = set()

        for raw_url in urls:
            if raw_url.lower().startswith("mailto:"):
                for email in _extract_emails(raw_url[7:]):
                    if email not in emails:
                        emails.append(email)
                        sources[email] = raw_url
                continue
            parsed = urlparse(raw_url)
            host = (parsed.hostname or "").casefold()
            if host in SOCIAL_HOSTS or not self.safety_check(raw_url):
                continue
            origin = f"{parsed.scheme}://{parsed.netloc}"
            if origin in visited_origins:
                continue
            visited_origins.add(origin)
            self._scan_site(raw_url, origin, emails, sources, scanned)

        return EmailDiscovery(emails, sources, scanned)

    def _scan_site(
        self,
        start_url: str,
        origin: str,
        emails: list[str],
        sources: dict[str, str],
        scanned: list[str],
    ) -> None:
        queue = [start_url]
        visited: set[str] = set()
        while queue and len(visited) < self.max_pages_per_site:
            url = queue.pop(0)
            if url in visited or not self.safety_check(url):
                continue
            visited.add(url)
            self.status(f"检查公开网页邮箱：{urlparse(url).netloc}")
            try:
                response = self.session.get(
                    url,
                    headers=self.headers,
                    timeout=self.timeout,
                    allow_redirects=True,
                )
            except requests.RequestException:
                continue
            final_url = getattr(response, "url", url)
            content_type = response.headers.get("Content-Type", "").casefold()
            content_length = int(response.headers.get("Content-Length", "0") or 0)
            if (
                not response.ok
                or (content_length and content_length > MAX_HTML_BYTES)
                or not any(kind in content_type for kind in ("text/html", "application/xhtml+xml", "text/plain"))
                or not self.safety_check(final_url)
            ):
                continue
            body = response.content[:MAX_HTML_BYTES].decode(response.encoding or "utf-8", errors="replace")
            scanned.append(final_url)
            parser = _PageParser()
            try:
                parser.feed(body)
            except Exception:
                pass
            page_text = "\n".join(parser.text)
            for email in _extract_emails(page_text):
                if email not in emails:
                    emails.append(email)
                    sources[email] = final_url

            for href, label in parser.links:
                if href.lower().startswith("mailto:"):
                    for email in _extract_emails(href[7:]):
                        if email not in emails:
                            emails.append(email)
                            sources[email] = final_url
                    continue
                if len(visited) == 1:
                    absolute = urljoin(final_url, href).split("#", 1)[0]
                    target = urlparse(absolute)
                    marker_text = f"{target.path} {label}".casefold()
                    if (
                        f"{target.scheme}://{target.netloc}" == origin
                        and any(marker in marker_text for marker in CONTACT_MARKERS)
                        and absolute not in visited
                        and absolute not in queue
                    ):
                        queue.append(absolute)
            if self.interval:
                time.sleep(self.interval)
