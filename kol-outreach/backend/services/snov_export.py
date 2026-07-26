"""Create a new Snov prospect list from selected pending KOL records."""
import re
from typing import Any

from sqlalchemy.orm import Session

from models import Kol


class SnovListCreateError(RuntimeError):
    """Raised before any prospect is written when Snov cannot create the list."""


def _created_list_id(payload: Any) -> str:
    candidates = payload if isinstance(payload, list) else [payload]
    for item in candidates:
        if not isinstance(item, dict) or item.get("success") is False:
            continue
        data = item.get("data") if isinstance(item.get("data"), dict) else item
        value = data.get("id") if isinstance(data, dict) else None
        if value not in (None, ""):
            return str(value)
    raise SnovListCreateError(f"营销平台未返回新名单 ID: {payload!r}")


def _valid_email(value: Any) -> bool:
    email = str(value or "").strip()
    return bool(email and "@" in email and " " not in email)


def _phones(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in re.split(r"[,;|\n]+", str(value or "")) if item.strip()]


def _prospect_payload(kol: Kol) -> dict:
    company_site = str(kol.company_site or "").strip()
    if company_site and not company_site.lower().startswith(("http://", "https://")):
        company_site = ""
    linkedin = str(kol.linkedin_url or "").strip()
    if linkedin and not linkedin.lower().startswith(("http://", "https://")):
        linkedin = ""
    return {
        "email": str(kol.email).strip().lower(),
        "fullName": kol.full_name or kol.name,
        "firstName": kol.first_name,
        "lastName": kol.last_name,
        "phones": _phones(kol.phones),
        "country": kol.country,
        "locality": kol.locality,
        "position": kol.position,
        "companyName": kol.company_name,
        "companySite": company_site or None,
        "linkedin_url": linkedin or None,
    }


def create_snov_list_from_kols(
    db: Session,
    client: Any,
    *,
    list_name: str,
    kol_ids: list[int],
) -> dict:
    """Validate selected KOLs, create one list, then add eligible contacts."""
    requested_ids = list(kol_ids)
    unique_ids = list(dict.fromkeys(requested_ids))
    kols = db.query(Kol).filter(Kol.id.in_(unique_ids)).all() if unique_ids else []
    by_id = {kol.id: kol for kol in kols}
    results: list[dict] = []
    eligible: list[Kol] = []
    seen: set[int] = set()

    for kol_id in requested_ids:
        if kol_id in seen:
            results.append({"kol_id": kol_id, "status": "skipped", "reason": "重复的 KOL ID"})
            continue
        seen.add(kol_id)
        kol = by_id.get(kol_id)
        if not kol:
            results.append({"kol_id": kol_id, "status": "skipped", "reason": "KOL 不存在"})
        elif kol.status != "pending":
            results.append({
                "kol_id": kol_id,
                "name": kol.name,
                "email": kol.email,
                "status": "skipped",
                "reason": "仅待发状态可推送",
            })
        elif not _valid_email(kol.email):
            results.append({
                "kol_id": kol_id,
                "name": kol.name,
                "email": kol.email,
                "status": "skipped",
                "reason": "缺少有效主邮箱",
            })
        else:
            eligible.append(kol)

    response = {
        "list_id": None,
        "list_name": list_name,
        "requested": len(requested_ids),
        "added": 0,
        "updated": 0,
        "unchanged": 0,
        "skipped": sum(item["status"] == "skipped" for item in results),
        "failed": 0,
        "successful_kol_ids": [],
        "results": results,
    }
    if not eligible:
        return response

    try:
        list_id = _created_list_id(client.create_prospect_list(list_name))
    except SnovListCreateError:
        raise
    except Exception as exc:
        raise SnovListCreateError(str(exc)) from exc
    response["list_id"] = list_id

    for kol in eligible:
        base = {"kol_id": kol.id, "name": kol.name, "email": kol.email}
        try:
            snov_result = client.add_prospect_to_list(list_id, _prospect_payload(kol))
            if not isinstance(snov_result, dict) or not snov_result.get("success"):
                reason = snov_result.get("errors") if isinstance(snov_result, dict) else snov_result
                raise RuntimeError(str(reason or "营销平台返回添加失败"))

            outcome = "added" if snov_result.get("added") else (
                "updated" if snov_result.get("updated") else "unchanged"
            )
            response[outcome] += 1
            response["successful_kol_ids"].append(kol.id)
            response["results"].append({**base, "status": outcome})

            prospect_id = snov_result.get("id")
            if prospect_id:
                kol.snov_prospect_id = str(prospect_id)
            kol.snov_list_id = list_id
            kol.snov_list_name = list_name
            list_ids = [str(value) for value in (kol.snov_list_ids or []) if value not in (None, "")]
            if list_id not in list_ids:
                list_ids.append(list_id)
            kol.snov_list_ids = list_ids
        except Exception as exc:
            response["failed"] += 1
            response["results"].append({**base, "status": "failed", "reason": str(exc)})

    db.commit()
    return response
