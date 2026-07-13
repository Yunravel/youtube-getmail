import re
from urllib.parse import urlparse


EMAIL_RE = re.compile(r"(?i)(?<![\w.+-])([\w.+-]+@[\w-]+(?:\.[\w-]+)+)")
URL_RE = re.compile(
    r"(?i)(?:https?://)?(?:www\.)?"
    r"(?:t\.me|telegram\.me|wa\.me|whatsapp\.com|x\.com|twitter\.com|"
    r"facebook\.com|fb\.com|instagram\.com|tiktok\.com)/[^\s<>'\"，。；;、)]+"
)

PLATFORM_HOSTS = {
    "telegram": ("t.me", "telegram.me"),
    "whatsapp": ("wa.me", "whatsapp.com"),
    "twitter": ("x.com", "twitter.com"),
    "facebook": ("facebook.com", "fb.com"),
    "instagram": ("instagram.com",),
    "tiktok": ("tiktok.com",),
}


def _normalize_url(raw: str) -> str:
    value = raw.rstrip(".,;:!?")
    if not value.lower().startswith(("http://", "https://")):
        value = "https://" + value
    return value


def extract_public_contacts(description: str) -> dict[str, str]:
    """Extract only contact details voluntarily published in a channel description."""
    text = description or ""
    result = {name: "" for name in PLATFORM_HOSTS}
    result["email"] = ""

    emails = list(dict.fromkeys(m.group(1) for m in EMAIL_RE.finditer(text)))
    result["email"] = " | ".join(emails)

    grouped: dict[str, list[str]] = {name: [] for name in PLATFORM_HOSTS}
    for match in URL_RE.finditer(text):
        url = _normalize_url(match.group(0))
        host = urlparse(url).netloc.lower().removeprefix("www.")
        for platform, hosts in PLATFORM_HOSTS.items():
            if host in hosts and url not in grouped[platform]:
                grouped[platform].append(url)
                break
    for platform, urls in grouped.items():
        result[platform] = " | ".join(urls)
    return result

