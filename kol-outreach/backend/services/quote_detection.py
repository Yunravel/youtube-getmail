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
    items = extract_money_items(body_text or "", "body")
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

    hinted_attachment = bool(_QUOTE_HINT_RE.search(body_text or "") and re.search(r"\battach", body_text or "", re.I))
    confirmed = bool(items)
    all_text = "\n".join([body_text or ""] + [item["text"] for item in attachment_texts])
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
