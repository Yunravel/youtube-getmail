"""Import Snov prospect lists into the local contact database."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import Kol
from services.country_normalize import normalize_country_for_storage
from services.email_utils import ensure_kol_email, normalize_email


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("name") or value.get("label") or value.get("value")
    value = str(value).strip()
    return value or None


def _first(payload: dict, *keys: str) -> Any:
    for key in keys:
        if payload.get(key) not in (None, "", [], {}):
            return payload[key]
    return None


def _custom_fields(payload: dict) -> dict[str, Any]:
    raw = _first(payload, "customFields", "custom_fields") or {}
    if isinstance(raw, dict):
        return raw
    result: dict[str, Any] = {}
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            key = _first(item, "name", "label", "key")
            if key:
                result[str(key).strip()] = _first(item, "value", "values")
    return result


def _custom(custom: dict[str, Any], key: str) -> Any:
    aliases = {
        key,
        f"customFields[{key}]",
        f"customFields['{key}']",
        f'customFields["{key}"]',
    }
    for name, value in custom.items():
        if str(name).strip() in aliases:
            return value
    return None


def _phone(payload: dict) -> str | None:
    value = _first(payload, "phones", "phone")
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = _first(value, "phone", "number", "value")
    return _text(value)


def _linkedin(payload: dict) -> str | None:
    links = _first(payload, "socialLinks", "social_links") or {}
    if isinstance(links, dict):
        return _text(_first(links, "linkedIn", "linkedin", "linked_in"))
    if isinstance(links, list):
        for item in links:
            if isinstance(item, dict) and str(item.get("type", "")).lower() == "linkedin":
                return _text(_first(item, "url", "link", "value"))
    return _text(_first(payload, "linkedin", "linkedinUrl", "linkedin_url"))


def _followers(value: Any) -> int:
    if isinstance(value, dict):
        value = value.get("value")
    try:
        return max(0, int(str(value or "0").replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0


def _emails(prospect: dict) -> list[str]:
    raw = _first(prospect, "emails", "email")
    if not isinstance(raw, list):
        raw = [raw]
    result: list[str] = []
    for item in raw:
        value = _first(item, "email", "value") if isinstance(item, dict) else item
        email = str(value or "").strip().lower()
        if "@" in email and email not in result:
            result.append(email)
    return result


def _mapped_fields(prospect: dict, list_id: str, list_name: str) -> dict[str, Any]:
    custom = _custom_fields(prospect)
    first_name = _text(_first(prospect, "firstName", "first_name"))
    last_name = _text(_first(prospect, "lastName", "last_name"))
    full_name = _text(_first(prospect, "fullName", "full_name", "name"))
    if not full_name:
        full_name = " ".join(x for x in (first_name, last_name) if x) or None
    priority = _text(_custom(custom, "priority"))
    if priority:
        priority = priority.upper()
        if priority not in {"P0", "P1", "P2", "P3"}:
            priority = None

    profile_url = _text(_custom(custom, "profileUrl"))
    followers = _followers(_custom(custom, "followers"))
    category = _text(_custom(custom, "contentCategory"))
    return {
        "snov_prospect_id": _text(_first(prospect, "id", "prospectId", "prospect_id")),
        "full_name": full_name,
        "first_name": first_name,
        "last_name": last_name,
        "country": normalize_country_for_storage(
            _text(_first(prospect, "country", "countryName", "country_name"))
        ),
        "locality": _text(_first(prospect, "locality", "city")),
        "position": _text(_first(prospect, "position", "jobTitle", "job_title")),
        "company_name": _text(_first(prospect, "companyName", "company_name", "company")),
        "company_site": _text(_first(prospect, "companySite", "company_site", "website")),
        "phones": _phone(prospect),
        "linkedin_url": _linkedin(prospect),
        "platform": _text(_custom(custom, "platform")),
        "social_handle": _text(_custom(custom, "socialHandle")),
        "profile_url": profile_url,
        "followers": followers,
        "priority": priority,
        "content_category": category,
        "source": _text(_custom(custom, "source")) or "Snov",
        "contact_notes": _text(_custom(custom, "notes")),
        "snov_list_id": list_id,
        "snov_list_name": list_name,
        "snov_custom_fields": custom or None,
        "snov_raw_data": prospect,
        "updated_at": datetime.utcnow(),
    }


def sync_snov_contacts(db: Session, client) -> dict[str, Any]:
    """Upsert all non-deleted Snov list contacts, deduplicated by lowercase email."""
    lists = [item for item in client.list_prospect_lists() if not item.get("isDeleted")]
    result: dict[str, Any] = {
        "lists": len(lists),
        "seen": 0,
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "errors": [],
    }
    try:
        for snov_list in lists:
            list_id = str(snov_list.get("id") or "").strip()
            list_name = str(snov_list.get("name") or "").strip()
            if not list_id:
                result["skipped"] += 1
                continue

            page = 1
            while True:
                try:
                    payload = client.get_prospects_in_list(list_id, page=page, per_page=5000)
                except Exception as exc:
                    result["errors"].append({"list_id": list_id, "error": str(exc)})
                    break
                prospects = payload.get("prospects") or []
                if not isinstance(prospects, list):
                    result["errors"].append({"list_id": list_id, "error": "invalid prospects payload"})
                    break

                for prospect in prospects:
                    if not isinstance(prospect, dict):
                        result["skipped"] += 1
                        continue
                    fields = _mapped_fields(prospect, list_id, list_name)
                    emails = _emails(prospect)
                    if not emails:
                        result["skipped"] += 1
                        continue
                    for idx, email in enumerate(emails):
                        result["seen"] += 1
                        kol = db.query(Kol).filter(func.lower(Kol.email) == email).first()
                        created = kol is None
                        if created:
                            name = fields.get("full_name") or email.split("@", 1)[0]
                            kol = Kol(name=name, email=email, status="pending")
                            db.add(kol)
                            # 需要主键 id 才能写 kol_email 子表（FK）。
                            db.flush()
                            result["created"] += 1
                        else:
                            result["updated"] += 1

                        # 同步 kol_email 子表：首个邮箱为主，其余为备用。
                        # 规范 §5.1：kol.email 是兼容性主邮箱投影，必须与 kol_email
                        # 保持一致，不能只写主表漏掉子表（这是历史 236 行漂移的根因）。
                        ensure_kol_email(
                            db,
                            kol.id,
                            email,
                            is_primary=(idx == 0),
                            source=f"snov_import | {list_name or list_id}",
                        )

                        list_ids = list(kol.snov_list_ids or [])
                        if list_id not in list_ids:
                            list_ids.append(list_id)
                        fields["snov_list_ids"] = list_ids
                        for key, value in fields.items():
                            if value not in (None, "", [], {}):
                                setattr(kol, key, value)

                        # Keep legacy fields populated for the current UI.
                        kol.name = fields.get("full_name") or kol.name
                        kol.channel_url = fields.get("profile_url") or kol.channel_url
                        kol.subscribers = fields.get("followers") or kol.subscribers or 0
                        kol.niche = fields.get("content_category") or kol.niche

                db.flush()
                total = int((payload.get("list") or {}).get("contacts") or 0)
                if not prospects or len(prospects) < 5000 or page * 5000 >= total:
                    break
                page += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    return result
