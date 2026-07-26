"""Deterministic quote extraction from email bodies and local attachments."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from config import settings


SUPPORTED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}
SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
SUPPORTED_PDF_TYPES = {"application/pdf"}
_QUOTE_HINT_RE = re.compile(r"\b(rate\s*card|rates?|pricing|quote|fee|cost|budget|package|option)\b", re.I)
_PAYMENT_TERM_RE = re.compile(
    r"[^\n]{0,100}\b(?:upfront|deposit|payment|net\s*\d+|before publication|after publication)\b[^\n]{0,140}",
    re.I,
)
_MONEY_RE = re.compile(
    r"(?P<prefix>USD|GBP|EUR|CAD|AUD|US\$|CA\$|AU\$|[$£€])\s*"
    r"(?P<amount>\d[\d,]*(?:\.\d+)?\s*[kKmM]?)"
    r"|(?P<amount_after>\d[\d,]*(?:\.\d+)?\s*[kKmM]?)\s*"
    r"(?P<suffix>USD|GBP|EUR|CAD|AUD)\b",
    re.I,
)

# --- Quoted-reply stripping --------------------------------------------------
# Money amounts inside quoted/forwarded reply blocks are almost always OUR
# outreach budget echoed back, never the KOL's quote. Detection therefore runs
# on the stripped body only; attachment text is never stripped.
_QUOTE_PREFIX_RE = re.compile(r"^\s*>")
_SIGNATURE_DELIM_RE = re.compile(r"^--\s*$")
_ORIGINAL_MESSAGE_RE = re.compile(
    r"^-{2,}\s*(?:original\s+message|forwarded\s+message)\s*-{2,}$", re.I
)
# Line ENDING with a "wrote:" style marker (Gmail EN "... wrote:", Gmail FR
# "... a écrit :", Chinese clients "... 写道："). The tail is the stable
# feature: HTML replies converted to text keep the intro line but lose the
# ">" prefixes, and Gmail may wrap the intro across two lines.
_WROTE_TAIL_RE = re.compile(r"(?:\bwrote|\ba\s+écrit|写道)\s*[:：]\s*$", re.I)
_WROTE_LEAD_RE = re.compile(r"^(?:on|le)\b|^在", re.I)
_CN_FROM_RE = re.compile(r"^(?:发件人|寄件人)\s*[:：]")
_FROM_HEADER_RE = re.compile(r"^from\s*:", re.I)
_SENT_HEADER_RE = re.compile(r"^(?:sent|date|发送时间|日期)\s*[:：]", re.I)


def _quote_intro_index(lines: list[str]) -> int | None:
    """Index of the first quote-intro line, or None when no intro is found."""
    for index, raw in enumerate(lines):
        # ">"-prefixed lines are removed wholesale by the caller; a quote intro
        # nested inside them must not become the truncation point, otherwise an
        # inline (interleaved) reply below the quote block would be lost too.
        if _QUOTE_PREFIX_RE.match(raw):
            continue
        line = raw.strip()
        if not line:
            continue
        if (
            _SIGNATURE_DELIM_RE.match(line)          # "--" / "-- " signature delimiter
            or _ORIGINAL_MESSAGE_RE.match(line)      # Outlook / Gmail forward divider
            or _CN_FROM_RE.match(line)               # 中文客户端引用头 "发件人："
            or _WROTE_TAIL_RE.search(line)           # single-line "On ... wrote:" 等
        ):
            return index
        following = [
            candidate.strip()
            for candidate in lines[index + 1 : index + 3]
            if not _QUOTE_PREFIX_RE.match(candidate)
        ]
        # Gmail wraps long intros: "On ... Team\n<us@example.com> wrote:".
        if _WROTE_LEAD_RE.match(line) and any(_WROTE_TAIL_RE.search(item) for item in following):
            return index
        # Outlook top-quote header: "From: ..." only counts when a
        # "Sent:"/"Date:" line follows within two lines, so a body sentence
        # merely starting with "From:" is not treated as a quote header.
        if _FROM_HEADER_RE.match(line) and any(_SENT_HEADER_RE.match(item) for item in following):
            return index
    return None


def strip_quoted_text(text: str) -> str:
    """Remove quoted/forwarded reply content from an email body.

    Rules (conservative, bias towards dropping quoted text — a missed quote
    degrades to human review, while a false positive triggers an auto reply):
    1. Truncate at the first quote-intro line ("On ... wrote:", "-----Original
       Message-----", "From:"+"Sent:" pair, "发件人：", "在 ... 写道：",
       "Le ... a écrit :", "--" signature delimiter).
    2. Drop every ">"-prefixed line (including "> " and nested ">>").
    3. Return "" when nothing remains (the mail was pure quote/forward).
    """
    if not text:
        return ""
    lines = text.splitlines()
    cut = _quote_intro_index(lines)
    kept = lines if cut is None else lines[:cut]
    kept = [line for line in kept if not _QUOTE_PREFIX_RE.match(line)]
    return "\n".join(kept).strip()


def _normalize_amount(raw: str) -> int | None:
    text = re.sub(r"[\s,]", "", raw or "")
    multiplier = 1
    if text and text[-1:].lower() in {"k", "m"}:
        multiplier = 1_000 if text[-1:].lower() == "k" else 1_000_000
        text = text[:-1]
    try:
        amount = int(float(text) * multiplier)
    except (TypeError, ValueError):
        return None
    return amount if amount >= 10 else None


def _normalize_currency(raw: str) -> str:
    value = (raw or "").upper()
    return {
        "$": "USD", "US$": "USD", "£": "GBP", "€": "EUR",
        "CA$": "CAD", "AU$": "AUD",
    }.get(value, value)


def extract_money_items(text: str, source: str = "body") -> list[dict[str, Any]]:
    """Return explicit amount+currency pairs with a short auditable excerpt."""
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    value = text or ""
    for match in _MONEY_RE.finditer(value):
        amount = _normalize_amount(match.group("amount") or match.group("amount_after") or "")
        currency = _normalize_currency(match.group("prefix") or match.group("suffix") or "")
        if not amount or not currency:
            continue
        line_start = max(value.rfind("\n", 0, match.start()) + 1, match.start() - 100)
        line_end_pos = value.find("\n", match.end())
        line_end = min(len(value), line_end_pos if line_end_pos >= 0 else match.end() + 100)
        evidence = re.sub(r"\s+", " ", value[line_start:line_end]).strip()[:240]
        identity = (currency, amount, evidence)
        if identity in seen:
            continue
        seen.add(identity)
        output.append({
            "currency": currency,
            "amount": amount,
            "evidence": evidence or match.group(0),
            "source": source,
        })
    return output


def _safe_attachment_path(local_path: str) -> Path | None:
    root = settings.attachment_storage_path.resolve()
    candidate = (root / local_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if not candidate.is_file() or candidate.stat().st_size > settings.AUTO_REPLY_MAX_ATTACHMENT_BYTES:
        return None
    return candidate


def _ocr_image(image) -> str:
    import pytesseract
    return pytesseract.image_to_string(image, lang="eng")


def _extract_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    text = "\n".join((page.extract_text() or "") for page in reader.pages[:20]).strip()
    if text:
        return text[:50_000]

    # Scanned PDF fallback: render a bounded number of pages, then OCR.
    import fitz
    from PIL import Image

    document = fitz.open(str(path))
    pages: list[str] = []
    for page in list(document)[:10]:
        pix = page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), alpha=False)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        pages.append(_ocr_image(image))
    return "\n".join(pages)[:50_000]


def extract_attachment_text(attachment: dict) -> tuple[str, str | None]:
    """Return (text, error). Remote-only and unsupported files are not fetched."""
    local_path = str(attachment.get("local_path") or "")
    if not local_path:
        return "", "attachment_not_synced"
    path = _safe_attachment_path(local_path)
    if not path:
        return "", "attachment_missing_or_too_large"
    content_type = str(attachment.get("content_type") or "").lower()
    suffix = path.suffix.lower()
    try:
        if content_type in SUPPORTED_PDF_TYPES or suffix == ".pdf":
            text = _extract_pdf(path)
            return (text, None) if text.strip() else ("", "attachment_text_empty")
        if content_type in SUPPORTED_IMAGE_TYPES or suffix in SUPPORTED_IMAGE_SUFFIXES:
            from PIL import Image
            with Image.open(path) as image:
                text = _ocr_image(image.convert("RGB"))[:50_000]
                return (text, None) if text.strip() else ("", "attachment_text_empty")
    except Exception as exc:
        return "", f"attachment_parse_failed: {exc}"
    return "", "unsupported_attachment"


def detect_quote(body_text: str, attachments: list[dict] | None = None) -> dict[str, Any]:
    """Detect an explicit quote in the direct (non-quoted) body and attachments.

    The body is stripped of quoted/forwarded reply blocks first, so budget
    figures from our own outreach mail echoed back in the quote block are not
    mistaken for the KOL's quote. Attachment text is never stripped: rate-card
    attachments are the main legitimate quote source.
    """
    raw_body = body_text or ""
    body = strip_quoted_text(raw_body)
    items = extract_money_items(body, "body")
    errors: list[str] = []
    pending = False
    attachment_texts: list[dict[str, str]] = []
    for attachment in attachments or []:
        text, error = extract_attachment_text(attachment)
        name = str(attachment.get("name") or "attachment")
        if error:
            errors.append(f"{name}: {error}")
            if error == "attachment_not_synced":
                pending = True
            continue
        attachment_texts.append({"name": name, "text": text[:10_000]})
        items.extend(extract_money_items(text, f"attachment:{name}"))

    hinted_attachment = bool(_QUOTE_HINT_RE.search(body) and re.search(r"\battach", body, re.I))
    confirmed = bool(items)
    all_text = "\n".join([body] + [item["text"] for item in attachment_texts])
    payment_terms = [re.sub(r"\s+", " ", match.group(0)).strip() for match in _PAYMENT_TERM_RE.finditer(all_text)]
    return {
        "confirmed": confirmed,
        "items": items,
        "deliverables": list(dict.fromkeys(item["evidence"] for item in items if item.get("evidence"))),
        "payment_terms": list(dict.fromkeys(payment_terms)),
        "sources": sorted({item["source"] for item in items}),
        "attachments_parsed": [item["name"] for item in attachment_texts],
        "errors": errors,
        "awaiting_attachment": not confirmed and (pending or hinted_attachment),
        # Informational, additive key: True when quoted/forwarded content was
        # removed from the body before detection (CRLF normalization ignored).
        "body_quoted_stripped": body != "\n".join(raw_body.splitlines()).strip(),
    }


def quote_summary(quote: dict[str, Any]) -> str:
    lines: list[str] = []
    for item in quote.get("items") or []:
        amount = f"{int(item['amount']):,}"
        evidence = str(item.get("evidence") or "").strip()
        line = f"- {item['currency']} {amount}"
        if evidence:
            line += f" — {evidence}"
        lines.append(line)
    return "\n".join(lines)
