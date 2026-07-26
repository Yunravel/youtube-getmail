"""Reliable one-KOL-one-row synchronization to Feishu Sheets.

The database is authoritative.  Feishu is a materialized operational view:

* profile fields are resolved from all available KOL aliases;
* commercial fields are aggregated across every inbound message in the thread;
* columns are matched by header name rather than fixed position;
* operator-owned columns are preserved on updates;
* ``系统KOL ID`` is the stable row key, with email as a legacy fallback;
* delivery is driven by the durable ``feishu_sync_task`` table.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx
from sqlalchemy import func

from config import settings
from db import SessionLocal
from models import FeishuSyncTask, Kol, Message, Thread

logger = logging.getLogger(__name__)

_MAX_CELL_TEXT = 2000
_MAX_SHEET_ROWS = 10000
INVALID_REPLY_INTENTS = frozenset({"auto_reply", "ooo"})
_AUTOMATIC_SUBJECT_MARKERS = (
    "automatic reply",
    "auto reply",
    "auto-reply",
    "out of office",
    "out of the office",
)
_AUTOMATIC_BODY_MARKERS = (
    "this inbox is not monitored",
    "this mailbox is not monitored",
    "do not reply to this email",
    "reference for your ticket",
    "your ticket number is",
    "we've successfully received your message",
    "we have successfully received your message",
    "currently out of the office",
    "currently away from the office",
)

# Canonical Feishu presentation order.  Header-name mapping remains
# authoritative, so harmless manual column moves do not break synchronization.
COLUMNS = [
    "代理商名称",
    "达人主要链接",
    "达人名",
    "达人标签",
    "平台",
    "回信所属项目",
    "粉丝数",
    "最近1个月发稿1次日期",
    "最近10条阅读数",
    "最近10条互动数",
    "达人账号认证信息",
    "应合全链路国家、设备、程别",
    "可接受接入方式",
    "完整报价",
    "最低报价",
    "意向",
    "邮箱",
    "回信时间",
    "系统KOL ID",
    "CPM",
]

# These columns are explicitly operator-owned and are never overwritten when
# an existing row is updated.
OPERATOR_COLUMNS = {
    "代理商名称",
    "最近1个月发稿1次日期",
    "最近10条阅读数",
    "最近10条互动数",
    "达人账号认证信息",
    "CPM",
}
MANAGED_COLUMNS = set(COLUMNS) - OPERATOR_COLUMNS
REQUIRED_HEADERS = set(COLUMNS[:18])
SYSTEM_HEADERS = {"系统KOL ID"}

# Header aliases tolerate harmless historical naming differences while still
# refusing ambiguous/unknown schemas.
HEADER_ALIASES = {
    "素类": "达人标签",
    "Type": "回信所属项目",
    "国家": "应合全链路国家、设备、程别",
    "国家/地区": "应合全链路国家、设备、程别",
    "平台报价": "完整报价",
    "平台报价（USD美元）": "完整报价",
    "平台报价(USD美元)": "完整报价",
    "对外报价": "最低报价",
    "时间戳": "回信时间",
    "KOL ID": "系统KOL ID",
}

PLACEHOLDERS = {
    "link": "待补全",
    "niche": "待分类",
    "platform": "待确认",
    "followers": "待采集",
    "country": "未确认",
    "commercial": "未提供",
}


class FeishuSyncError(RuntimeError):
    """A retryable external synchronization failure."""


class FeishuSchemaError(FeishuSyncError):
    """The target sheet cannot be safely matched to the canonical schema."""


def _message_intent(message: Message) -> str:
    return str((message.ai_analysis or {}).get("intent") or "").strip().lower()


def _is_invalid_reply(message: Message) -> bool:
    """Automatic acknowledgements and out-of-office notices are not KOL replies."""
    if message.direction != "inbound":
        return False
    if _message_intent(message) in INVALID_REPLY_INTENTS:
        return True
    subject = str(message.subject or "").strip().lower()
    body = str(message.body_text or "").strip().lower()
    return any(marker in subject for marker in _AUTOMATIC_SUBJECT_MARKERS) or any(
        marker in body for marker in _AUTOMATIC_BODY_MARKERS
    )


def _column_letter(index: int) -> str:
    """Convert a zero-based column index to an Excel/Sheets column name."""
    result = ""
    number = index + 1
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _normalize_header(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value or "")).strip()
    for source, target in HEADER_ALIASES.items():
        if text == re.sub(r"\s+", "", source):
            return target
    for canonical in COLUMNS:
        if text == re.sub(r"\s+", "", canonical):
            return canonical
    return str(value or "").strip()


def _truncate(value: Any, limit: int = _MAX_CELL_TEXT) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text if len(text) <= limit else text[:limit]


def _cell_text(value: Any) -> str:
    """Extract plain text from Feishu scalar or hyperlink cell values."""
    if value is None:
        return ""
    if isinstance(value, list):
        pieces = []
        for item in value:
            if isinstance(item, dict):
                pieces.append(str(item.get("text") or item.get("link") or ""))
            elif item is not None:
                pieces.append(str(item))
        return "".join(pieces).strip()
    if isinstance(value, dict):
        return str(value.get("text") or value.get("link") or "").strip()
    return str(value).strip()


def _cell_email(value: Any) -> str:
    text = _cell_text(value)
    if text.lower().startswith("mailto:"):
        text = text[7:]
    return text.strip()


class FeishuClient:
    """Feishu Sheets client with schema discovery and stable row upserts."""

    def __init__(
        self,
        app_id: Optional[str] = None,
        app_secret: Optional[str] = None,
    ):
        self.app_id = app_id or settings.FEISHU_APP_ID
        self.app_secret = app_secret or settings.FEISHU_APP_SECRET
        self.base_url = settings.FEISHU_API_BASE.rstrip("/").replace("/open-apis", "")
        self.spreadsheet_token = settings.FEISHU_SPREADSHEET_TOKEN
        self.sheet_id = settings.FEISHU_SHEET_ID
        self._token: Optional[str] = None
        self._token_expires_at = 0.0
        self._sheet_verified = False
        self._schema_cache: Optional[dict[str, int]] = None
        self._client = httpx.Client(base_url=self.base_url, timeout=30, trust_env=True)

    def _get_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        if not self.app_id or not self.app_secret:
            raise FeishuSyncError("FEISHU_APP_ID / FEISHU_APP_SECRET 未配置")
        response = self._client.post(
            "/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("tenant_access_token")
        if not token:
            raise FeishuSyncError(f"飞书未返回 tenant_access_token: {payload}")
        self._token = token
        expires_in = int(payload.get("expire") or 7200)
        self._token_expires_at = time.monotonic() + max(60, expires_in - 60)
        return token

    def _authorized_request(self, method: str, path: str, **kwargs) -> dict:
        token = self._get_token()
        for attempt in range(4):
            headers = dict(kwargs.pop("headers", {}))
            headers["Authorization"] = f"Bearer {token}"
            response = self._client.request(method, path, headers=headers, **kwargs)
            if response.status_code == 401:
                self._token = None
                self._token_expires_at = 0
                token = self._get_token()
                continue
            if response.status_code >= 400:
                raise FeishuSyncError(
                    f"飞书 HTTP {response.status_code}: {response.text[:500]}"
                )
            payload = response.json()
            code = payload.get("code", 0)
            if code == 0:
                return payload
            if code == 90217 and attempt < 3:
                wait = 1.5 * (attempt + 1)
                logger.warning("飞书限流，%.1fs 后重试", wait)
                time.sleep(wait)
                continue
            raise FeishuSyncError(f"飞书业务错误 code={code}: {payload.get('msg')}")
        raise FeishuSyncError("飞书请求重试次数已用尽")

    def _resolve_sheet_id(self, token: Optional[str] = None) -> str:
        """Validate configured sheet ID; fall back to the first live sheet."""
        if self._sheet_verified and self.sheet_id:
            return self.sheet_id
        payload = self._authorized_request(
            "GET",
            f"/open-apis/sheets/v3/spreadsheets/{self.spreadsheet_token}/sheets/query",
        )
        sheets = payload.get("data", {}).get("sheets", [])
        if not sheets:
            raise FeishuSchemaError("电子表格没有工作表")
        live_ids = {str(item.get("sheet_id")) for item in sheets}
        if self.sheet_id and self.sheet_id not in live_ids:
            fallback = str(sheets[0]["sheet_id"])
            logger.warning(
                "配置的 FEISHU_SHEET_ID=%s 不存在，自动改用第一张工作表 %s",
                self.sheet_id,
                fallback,
            )
            self.sheet_id = fallback
        elif not self.sheet_id:
            self.sheet_id = str(sheets[0]["sheet_id"])
        self._sheet_verified = True
        return self.sheet_id

    def _get_values(self, range_a1: str) -> list[list[Any]]:
        sheet_id = self._resolve_sheet_id()
        payload = self._authorized_request(
            "GET",
            f"/open-apis/sheets/v2/spreadsheets/{self.spreadsheet_token}"
            f"/values/{sheet_id}!{range_a1}",
        )
        return payload.get("data", {}).get("valueRange", {}).get("values", []) or []

    def _put_values(self, range_a1: str, values: list[list[Any]]) -> None:
        sheet_id = self._resolve_sheet_id()
        self._authorized_request(
            "PUT",
            f"/open-apis/sheets/v2/spreadsheets/{self.spreadsheet_token}/values",
            json={
                "valueRange": {
                    "range": f"{sheet_id}!{range_a1}",
                    "values": values,
                }
            },
        )

    def _append_values(self, values: list[Any]) -> dict:
        sheet_id = self._resolve_sheet_id()
        last_col = _column_letter(len(values) - 1)
        payload = self._authorized_request(
            "POST",
            f"/open-apis/sheets/v2/spreadsheets/{self.spreadsheet_token}"
            "/values_append?insertDataOption=INSERT_ROWS",
            json={
                "valueRange": {
                    "range": f"{sheet_id}!A1:{last_col}1",
                    "values": [values],
                }
            },
        )
        return payload.get("data", {}).get("updates", {}) or {}

    def ensure_schema(self, refresh: bool = False) -> dict[str, int]:
        """Return canonical-header -> actual-column mapping.

        Existing business headers must be recognizable.  Stable system headers
        are appended automatically, without moving or overwriting business
        columns.
        """
        if self._schema_cache is not None and not refresh:
            return dict(self._schema_cache)
        # Real operational sheets often contain many manual columns after the
        # 18 canonical fields.  Scan far enough to rediscover system columns
        # even when they land beyond AZ (for example BA/BB).
        rows = self._get_values("A1:ZZ1")
        headers = list(rows[0]) if rows else []
        while headers and not _cell_text(headers[-1]):
            headers.pop()
        if not headers:
            headers = list(COLUMNS)
            self._put_values(f"A1:{_column_letter(len(headers) - 1)}1", [headers])

        mapping: dict[str, int] = {}
        duplicates: set[str] = set()
        renamed = False
        for index, raw in enumerate(headers):
            raw_text = _cell_text(raw)
            canonical = _normalize_header(raw_text)
            if canonical in COLUMNS:
                if canonical in mapping:
                    duplicates.add(canonical)
                mapping[canonical] = index
                if raw_text != canonical:
                    headers[index] = canonical
                    renamed = True
        if duplicates:
            raise FeishuSchemaError(
                "飞书表头存在重复列: " + ", ".join(sorted(duplicates))
            )
        missing_business = REQUIRED_HEADERS - set(mapping)
        if missing_business:
            raise FeishuSchemaError(
                "飞书表头缺少必要列: " + ", ".join(sorted(missing_business))
            )

        added = False
        for header in COLUMNS[-2:]:
            if header not in mapping:
                mapping[header] = len(headers)
                headers.append(header)
                added = True
        if added or renamed:
            self._put_values(f"A1:{_column_letter(len(headers) - 1)}1", [headers])
        self._schema_cache = mapping
        return dict(mapping)

    def _find_rows_in_column(self, column_index: int, target: str) -> list[int]:
        col = _column_letter(column_index)
        rows = self._get_values(f"{col}2:{col}{_MAX_SHEET_ROWS}")
        expected = str(target).strip().lower()
        found = []
        for row_num, row in enumerate(rows, start=2):
            if row and _cell_text(row[0]).strip().lower() == expected:
                found.append(row_num)
        return found

    def _find_unique_row(
        self,
        column_index: int,
        target: str,
        label: str,
    ) -> Optional[int]:
        rows = self._find_rows_in_column(column_index, target)
        if len(rows) > 1:
            raise FeishuSchemaError(
                f"飞书存在重复{label}，拒绝任选一行覆盖: {target} rows={rows}"
            )
        return rows[0] if rows else None

    def find_row(self, record: dict[str, Any]) -> Optional[int]:
        schema = self.ensure_schema()
        kol_id = record.get("系统KOL ID")
        if kol_id not in (None, ""):
            found = self._find_unique_row(
                schema["系统KOL ID"], str(kol_id), "系统KOL ID"
            )
            if found:
                return found
        email = _cell_email(record.get("邮箱")).lower()
        if email:
            return self._find_unique_row(schema["邮箱"], email, "邮箱")
        return None

    def find_row_by_email(self, email: str) -> Optional[int]:
        schema = self.ensure_schema()
        return self._find_unique_row(
            schema["邮箱"], _cell_email(email).lower(), "邮箱"
        )

    def _merge_record(
        self,
        record: dict[str, Any],
        existing_values: Optional[list[Any]] = None,
    ) -> list[Any]:
        schema = self.ensure_schema()
        width = max(schema.values()) + 1
        values = list(existing_values or [])
        values.extend([""] * (width - len(values)))
        for header in MANAGED_COLUMNS:
            values[schema[header]] = record.get(header, "")
        return values

    def update_record(self, row_num: int, record: dict[str, Any]) -> None:
        schema = self.ensure_schema()
        last_col = _column_letter(max(schema.values()))
        rows = self._get_values(f"A{row_num}:{last_col}{row_num}")
        existing = list(rows[0]) if rows else []
        merged = self._merge_record(record, existing)
        self._put_values(f"A{row_num}:{last_col}{row_num}", [merged])

    def append_record(self, record: dict[str, Any]) -> dict:
        return self._append_values(self._merge_record(record))

    def upsert_record(self, record: dict[str, Any]) -> tuple[str, int]:
        row_num = self.find_row(record)
        if row_num:
            self.update_record(row_num, record)
            return "updated", row_num
        updates = self.append_record(record)
        # Feishu's response shape differs slightly between API revisions.
        row_num = _row_number_from_updates(updates) or 0
        return "appended", row_num

    # Compatibility wrappers for older callers.
    def append_row(self, values: list[Any]) -> Optional[dict]:
        try:
            record = dict(zip(COLUMNS, values))
            return self.append_record(record)
        except Exception:
            logger.exception("飞书追加失败")
            return None

    def update_row(self, row_num: int, values: list[Any]) -> bool:
        try:
            self.update_record(row_num, dict(zip(COLUMNS, values)))
            return True
        except Exception:
            logger.exception("飞书更新失败")
            return False

    def upsert_row(self, email: str, values: list[Any]) -> str:
        record = dict(zip(COLUMNS, values))
        record["邮箱"] = email
        try:
            action, _ = self.upsert_record(record)
            return action
        except Exception:
            logger.exception("飞书 upsert 失败")
            return ""

    def close(self) -> None:
        self._client.close()


def _row_number_from_updates(updates: dict) -> Optional[int]:
    for key in ("updatedRange", "updated_range", "range"):
        value = updates.get(key)
        if value:
            match = re.search(r"[A-Z]+(\d+)", str(value))
            if match:
                return int(match.group(1))
    return None


_client: Optional[FeishuClient] = None


def get_feishu_client() -> FeishuClient:
    global _client
    if _client is None:
        _client = FeishuClient()
    return _client


def _first_value(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, dict)) and not value:
            continue
        return value
    return None


def _infer_platform(*urls: Any) -> Optional[str]:
    joined = " ".join(str(value or "").lower() for value in urls)
    for needle, platform in (
        ("youtube.com", "YouTube"),
        ("youtu.be", "YouTube"),
        ("instagram.com", "Instagram"),
        ("tiktok.com", "TikTok"),
        ("twitter.com", "X"),
        ("x.com", "X"),
        ("linkedin.com", "LinkedIn"),
    ):
        if needle in joined:
            return platform
    return None


def _analysis_value(messages: list[Message], *keys: str) -> Any:
    for message in sorted(
        messages,
        key=lambda item: item.received_at or datetime.min,
        reverse=True,
    ):
        analysis = message.ai_analysis or {}
        for key in keys:
            value = analysis.get(key)
            if value not in (None, "", [], {}):
                return value
    return None


def _split_tags(value: Any) -> list[str]:
    if value in (None, "", [], {}):
        return []
    raw_values = value if isinstance(value, list) else [value]
    tags: list[str] = []
    for raw in raw_values:
        for part in re.split(r"[,，、/|;\n]+", str(raw)):
            tag = re.sub(r"\s+", " ", part).strip(" -–—")
            if not tag or len(tag) > 30:
                continue
            if tag.lower() not in {item.lower() for item in tags}:
                tags.append(tag)
    return tags


def _creator_tags(kol: Kol, messages: list[Message], platform: str) -> str:
    """生成 2–3 个固定枚举达人标签（DATABASE_DEVELOPMENT.md §5.4）。

    从客观数据派生：赛道(kol.niche，已归一) + 平台 + 粉丝量级/语言。
    不再用 AI 自由文本（creator_niche/content_focus）和硬编码凑数标签
    （内容创作者/数字内容/品牌合作达人），确保标签可控、稳定、有统一范围。
    messages 参数保留以兼容调用方签名，当前不再使用。
    """
    from services.creator_tag import build_creator_tags
    from services.niche_normalize import normalize_niche_for_storage

    # niche 优先，为空回退 content_category；都过一遍归一确保是规范赛道枚举。
    niche = normalize_niche_for_storage(kol.niche) or normalize_niche_for_storage(kol.content_category)
    return build_creator_tags(
        niche=niche,
        platform=platform or kol.platform,
        followers=kol.subscribers or kol.followers,
        language=kol.language,
    )


def _complete_quote(messages: list[Message]) -> Optional[str]:
    """Return the latest complete rate card, with legacy fallbacks."""
    pieces: list[str] = []
    ordered = sorted(
        messages,
        key=lambda item: item.received_at or datetime.min,
        reverse=True,
    )
    for message in ordered:
        analysis = message.ai_analysis or {}
        authoritative = _format_quote(analysis.get("complete_quote"))
        if authoritative:
            return authoritative
        values = [
            analysis.get("deliverables"),
            analysis.get("platform_rate"),
            analysis.get("external_rate"),
            analysis.get("budget_mentioned"),
        ]
        quote = analysis.get("quote") or {}
        values.extend(quote.get("deliverables") or [])
        for value in values:
            formatted = _format_quote(value)
            if formatted and formatted not in pieces:
                pieces.append(formatted)
    return _truncate("; ".join(pieces)) if pieces else None


def _minimum_quote(
    messages: list[Message],
    complete_quote: Optional[str],
) -> Optional[str]:
    """Calculate the minimum amount per currency from structured quote items."""
    minimums: dict[str, float] = {}
    for message in messages:
        quote = (message.ai_analysis or {}).get("quote") or {}
        for item in quote.get("items") or []:
            if not isinstance(item, dict) or item.get("amount") is None:
                continue
            try:
                amount = float(item["amount"])
            except (TypeError, ValueError):
                continue
            currency = str(item.get("currency") or "").upper().strip()
            if not currency:
                continue
            minimums[currency] = min(minimums.get(currency, amount), amount)
    if not minimums and complete_quote:
        from services.ai_profile import parse_min_quote

        amount, symbol = parse_min_quote(complete_quote)
        if amount is not None:
            currency = {"$": "USD", "£": "GBP"}.get(symbol or "", symbol or "")
            minimums[currency or ""] = float(amount)
    if not minimums:
        return None

    currency_order = {"USD": 0, "GBP": 1, "EUR": 2}
    parts = []
    for currency, amount in sorted(
        minimums.items(),
        key=lambda item: (currency_order.get(item[0], 99), item[0]),
    ):
        rendered = f"{amount:,.2f}".rstrip("0").rstrip(".")
        symbol = {"USD": "$", "GBP": "£", "EUR": "€"}.get(currency)
        parts.append(f"{symbol}{rendered}" if symbol else f"{currency} {rendered}".strip())
    return " / ".join(parts)


def _format_quote(value: Any) -> Optional[str]:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, str):
        return _truncate(value)
    if isinstance(value, list):
        return _truncate("; ".join(str(item) for item in value if item))
    if isinstance(value, dict):
        items = value.get("items") or []
        pieces = []
        for item in items:
            if not isinstance(item, dict):
                continue
            amount = item.get("amount")
            currency = item.get("currency") or ""
            evidence = item.get("evidence") or ""
            if evidence:
                pieces.append(str(evidence))
            elif amount is not None:
                pieces.append(f"{currency} {amount}".strip())
        return _truncate("; ".join(piece for piece in pieces if piece)) or None
    return _truncate(value)


def _build_record(message: Message) -> dict[str, Any]:
    """Build a complete canonical record from a KOL's full conversation."""
    if not message.thread or not message.thread.kol:
        raise ValueError("回信缺少 thread/KOL 关联")
    if _is_invalid_reply(message):
        raise ValueError("自动回复或休假回复不能进入最终飞书表格")
    kol = message.thread.kol
    inbound = [
        item
        for item in message.thread.messages
        if item.direction == "inbound" and not _is_invalid_reply(item)
    ]
    if message not in inbound:
        inbound.append(message)

    email = _first_value(kol.email, message.from_email)
    received = max(
        (item.received_at for item in inbound if item.received_at),
        default=message.received_at,
    )
    if not email:
        raise ValueError("邮箱为空，禁止同步")
    if not received:
        raise ValueError("入站时间为空，禁止同步")

    link = _first_value(
        kol.channel_url,
        kol.profile_url,
        kol.source_link,
        kol.company_site,
    )
    name = _first_value(
        kol.name,
        kol.full_name,
        kol.company_name,
        kol.social_handle,
        str(email).split("@", 1)[0],
    )
    if not name:
        raise ValueError("达人名为空，禁止同步")

    niche = _first_value(
        kol.niche,
        kol.content_category,
        _analysis_value(inbound, "creator_niche", "content_focus"),
    )
    platform = _first_value(
        kol.platform,
        _infer_platform(link, kol.profile_url, kol.source_link),
    )
    followers = _first_value(
        kol.subscribers if kol.subscribers else None,
        kol.followers if kol.followers else None,
    )
    collaboration = _analysis_value(
        inbound,
        "collaboration_type",
        "deliverables",
    )
    platform_rate = _analysis_value(
        inbound,
        "platform_rate",
        "deliverables",
        "budget_mentioned",
        "quote",
    )
    external_rate = _analysis_value(inbound, "external_rate")
    complete_quote = _complete_quote(inbound)
    minimum_quote = _minimum_quote(inbound, complete_quote)
    tags = _creator_tags(kol, inbound, str(platform or ""))
    project = _first_value(
        message.thread.campaign_name,
        message.thread.campaign_id,
        kol.fit_project_code,
    )
    intent = _analysis_value(inbound, "intent") or message.thread.last_intent

    return {
        "代理商名称": "",
        "达人主要链接": _truncate(link) or PLACEHOLDERS["link"],
        "达人名": _truncate(name),
        "达人标签": tags or PLACEHOLDERS["niche"],
        "平台": _truncate(platform) or PLACEHOLDERS["platform"],
        "回信所属项目": _truncate(project) or "未确认",
        "粉丝数": followers if followers is not None else PLACEHOLDERS["followers"],
        "最近1个月发稿1次日期": "",
        "最近10条阅读数": "",
        "最近10条互动数": "",
        "达人账号认证信息": "",
        "应合全链路国家、设备、程别": _truncate(kol.country) or PLACEHOLDERS["country"],
        "可接受接入方式": _format_quote(collaboration) or PLACEHOLDERS["commercial"],
        "完整报价": complete_quote or PLACEHOLDERS["commercial"],
        "最低报价": minimum_quote or PLACEHOLDERS["commercial"],
        "意向": _truncate(intent) or "未分析",
        "邮箱": str(email).strip().lower(),
        "回信时间": received.isoformat(),
        "系统KOL ID": kol.id,
        "CPM": "",
    }


def _build_row(message: Message) -> list[Any]:
    record = _build_record(message)
    return [record[column] for column in COLUMNS]


def _payload_hash(record: dict[str, Any]) -> str:
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def enqueue_message_sync(
    message_id: int,
    delay_seconds: int = 0,
    db=None,
) -> bool:
    """Persist or refresh one KOL's synchronization task.

    With a borrowed ``db`` session changes are only flushed; commit,
    rollback and close remain the caller's responsibility so its
    transaction boundary stays intact.
    """
    owns_session = db is None
    session = db or SessionLocal()
    try:
        message = session.get(Message, message_id)
        if (
            not message
            or message.direction != "inbound"
            or not message.thread
            or not message.thread.kol_id
            or _is_invalid_reply(message)
        ):
            return False
        due = datetime.utcnow() + timedelta(seconds=max(0, delay_seconds))
        task = (
            session.query(FeishuSyncTask)
            .filter(FeishuSyncTask.kol_id == message.thread.kol_id)
            .one_or_none()
        )
        if task is None:
            task = FeishuSyncTask(
                kol_id=message.thread.kol_id,
                source_message_id=message.id,
                status="pending",
                next_retry_at=due,
            )
            session.add(task)
        else:
            task.source_message_id = message.id
            # A known duplicate-row conflict requires an operator decision.
            # Periodic reconciliation must not turn it back into an endless
            # retry loop.  POST /api/feishu/retry-conflicts releases it after
            # the sheet has been corrected.
            if task.status != "conflict":
                task.status = "pending"
                task.next_retry_at = due
                task.last_error = None
        if owns_session:
            session.commit()
        else:
            session.flush()
        return True
    except Exception:
        if owns_session:
            session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


def enqueue_messages_sync(
    message_ids: list[int],
    delay_seconds: int = 0,
) -> int:
    queued = 0
    for message_id in dict.fromkeys(message_ids):
        if enqueue_message_sync(message_id, delay_seconds=delay_seconds):
            queued += 1
    logger.info("飞书持久同步任务已入队: %s", queued)
    return queued


def enqueue_reconciliation() -> int:
    """Queue the latest valid human reply for every KOL.

    KOLs whose only inbound messages are automatic acknowledgements or
    out-of-office notices are explicitly quarantined as ``excluded``.
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(Message, Thread.kol_id)
            .join(Thread, Message.thread_id == Thread.id)
            .filter(Message.direction == "inbound")
            .order_by(Thread.kol_id, Message.received_at.desc(), Message.id.desc())
            .all()
        )
        latest_valid: dict[int, int] = {}
        latest_any: dict[int, int] = {}
        for message, kol_id in rows:
            latest_any.setdefault(kol_id, message.id)
            if not _is_invalid_reply(message):
                latest_valid.setdefault(kol_id, message.id)

        excluded_kol_ids = set(latest_any) - set(latest_valid)
        if excluded_kol_ids:
            tasks = (
                db.query(FeishuSyncTask)
                .filter(FeishuSyncTask.kol_id.in_(excluded_kol_ids))
                .all()
            )
            now = datetime.utcnow()
            for task in tasks:
                task.source_message_id = latest_any[task.kol_id]
                task.status = "excluded"
                task.last_error = "自动回复或休假回复，不进入最终飞书表格"
                task.next_retry_at = now
                task.updated_at = now
            db.commit()
        ids = list(latest_valid.values())
    finally:
        db.close()
    return enqueue_messages_sync(ids)


def _sync_task(task_id: int) -> tuple[str, int, str]:
    """同步单个任务到飞书。

    遵循 DATABASE_DEVELOPMENT.md §8：飞书 HTTP 调用不得在数据库事务/session 内进行。
    流程：短事务读 task+message 并构建 record（record 是纯 dict，session 关闭后仍可用）
    → 关闭 session → 调飞书 upsert_record（可能多次 HTTP 往返）→ 新短事务写结果。
    """
    # ---- 读取阶段：构建 record 与 payload_hash（须在 session 内完成惰性加载）----
    db = SessionLocal()
    try:
        task = db.get(FeishuSyncTask, task_id)
        if not task:
            raise ValueError(f"飞书同步任务不存在: {task_id}")
        source_message_id = task.source_message_id
        message = db.get(Message, source_message_id)
        if not message:
            raise ValueError(f"源回信不存在: {source_message_id}")
        record = _build_record(message)
        payload_hash = _payload_hash(record)
    finally:
        db.close()

    # ---- HTTP 阶段：无 DB session 持有 ----
    action, row_num = get_feishu_client().upsert_record(record)

    # ---- 写结果阶段：新短事务 ----
    db = SessionLocal()
    try:
        task = db.get(FeishuSyncTask, task_id)
        if not task:
            # 极端情况：任务在 HTTP 期间被删。记录后直接返回，避免覆盖。
            logger.warning("飞书同步任务在 HTTP 期间消失: %s", task_id)
            return action, row_num, record["达人名"]
        task.status = "synced"
        task.attempt_count = 0
        task.last_error = None
        task.payload_hash = payload_hash
        task.feishu_row_number = row_num or task.feishu_row_number
        task.synced_at = datetime.utcnow()
        task.updated_at = datetime.utcnow()
        db.commit()
        return action, row_num, record["达人名"]
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def process_due_tasks(limit: int = 50) -> dict[str, int]:
    """Process durable pending/retry tasks and retain failures for retry."""
    result = {"processed": 0, "synced": 0, "failed": 0, "disabled": 0}
    if not settings.feishu_is_configured:
        result["disabled"] = 1
        return result
    now = datetime.utcnow()
    db = SessionLocal()
    try:
        query = (
            db.query(FeishuSyncTask)
            .filter(FeishuSyncTask.status.in_(("pending", "retry", "processing")))
            .filter(FeishuSyncTask.next_retry_at <= now)
            .order_by(FeishuSyncTask.next_retry_at, FeishuSyncTask.id)
            .limit(limit)
        )
        if db.get_bind().dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        tasks = query.all()
        task_ids = [task.id for task in tasks]
        for task in tasks:
            task.status = "processing"
        db.commit()
    finally:
        db.close()

    for task_id in task_ids:
        result["processed"] += 1
        try:
            action, row_num, name = _sync_task(task_id)
            result["synced"] += 1
            logger.info(
                "飞书同步完成: task=%s action=%s row=%s kol=%s",
                task_id,
                action,
                row_num,
                name,
            )
        except Exception as exc:
            result["failed"] += 1
            retry_db = SessionLocal()
            try:
                task = retry_db.get(FeishuSyncTask, task_id)
                if task:
                    task.attempt_count = (task.attempt_count or 0) + 1
                    is_conflict = (
                        isinstance(exc, FeishuSchemaError)
                        and "重复" in str(exc)
                    )
                    if is_conflict:
                        task.status = "conflict"
                    else:
                        delay = min(
                            3600,
                            30 * (2 ** min(task.attempt_count - 1, 7)),
                        )
                        task.status = "retry"
                        task.next_retry_at = datetime.utcnow() + timedelta(
                            seconds=delay
                        )
                    task.last_error = str(exc)[:2000]
                    task.updated_at = datetime.utcnow()
                    retry_db.commit()
            finally:
                retry_db.close()
            logger.exception("飞书同步失败，已安排重试: task=%s", task_id)
    return result


def retry_conflicts() -> int:
    """Release operator-reviewed conflicts back to the delivery queue."""
    db = SessionLocal()
    try:
        tasks = (
            db.query(FeishuSyncTask)
            .filter(FeishuSyncTask.status == "conflict")
            .all()
        )
        now = datetime.utcnow()
        for task in tasks:
            task.status = "pending"
            task.next_retry_at = now
            task.last_error = None
            task.updated_at = now
        db.commit()
        return len(tasks)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def push_reply_to_feishu(message_id: int) -> None:
    """Compatibility entry point: persist first, then process due work."""
    try:
        enqueue_message_sync(message_id)
        process_due_tasks()
    except Exception:
        logger.exception("回信同步飞书异常: message=%s", message_id)


def push_after_delay(message_id: int, seconds: int = 120) -> None:
    """Compatibility entry point without sleeping in a worker thread."""
    try:
        enqueue_message_sync(message_id, delay_seconds=seconds)
    except Exception:
        logger.exception("回信飞书延迟任务入队失败: message=%s", message_id)


def synchronization_status() -> dict[str, Any]:
    db = SessionLocal()
    try:
        counts = dict(
            db.query(FeishuSyncTask.status, func.count(FeishuSyncTask.id))
            .group_by(FeishuSyncTask.status)
            .all()
        )
        last_success = db.query(func.max(FeishuSyncTask.synced_at)).scalar()
        return {
            "configured": bool(settings.feishu_is_configured),
            "counts": counts,
            "last_success_at": last_success,
        }
    finally:
        db.close()


def audit_sheet() -> dict[str, Any]:
    """Read-only reconciliation summary without returning contact values.

    This deliberately performs no writes.  Duplicate groups are identified by
    row numbers only so the result is safe to expose to the operations UI.
    """
    if not settings.feishu_is_configured:
        raise FeishuSyncError("飞书未配置")
    client = get_feishu_client()
    header_rows = client._get_values("A1:ZZ1")
    headers = list(header_rows[0]) if header_rows else []
    schema: dict[str, int] = {}
    duplicates: set[str] = set()
    for index, raw in enumerate(headers):
        canonical = _normalize_header(_cell_text(raw))
        if canonical not in COLUMNS:
            continue
        if canonical in schema:
            duplicates.add(canonical)
        schema[canonical] = index
    if duplicates:
        raise FeishuSchemaError(
            "飞书表头存在重复列: " + ", ".join(sorted(duplicates))
        )
    missing_headers = set(COLUMNS) - set(schema)
    if missing_headers:
        raise FeishuSchemaError(
            "只读对账要求表头完整，缺少: "
            + ", ".join(sorted(missing_headers))
        )
    last_col = _column_letter(max(schema.values()))
    rows = client._get_values(f"A1:{last_col}{_MAX_SHEET_ROWS}")
    data_rows = rows[1:] if rows else []

    email_rows: dict[str, list[int]] = {}
    kol_id_rows: dict[str, list[int]] = {}
    nonempty_rows = 0
    for row_num, row in enumerate(data_rows, start=2):
        if not any(_cell_text(value) for value in row):
            continue
        nonempty_rows += 1
        email_index = schema["邮箱"]
        kol_id_index = schema["系统KOL ID"]
        email = (
            _cell_email(row[email_index]).lower()
            if email_index < len(row)
            else ""
        )
        kol_id = (
            _cell_text(row[kol_id_index])
            if kol_id_index < len(row)
            else ""
        )
        if email:
            email_rows.setdefault(email, []).append(row_num)
        if kol_id:
            kol_id_rows.setdefault(kol_id, []).append(row_num)

    duplicate_email_rows = [
        row_numbers
        for row_numbers in email_rows.values()
        if len(row_numbers) > 1
    ]
    duplicate_kol_id_rows = [
        row_numbers
        for row_numbers in kol_id_rows.values()
        if len(row_numbers) > 1
    ]

    db = SessionLocal()
    try:
        source_rows = (
            db.query(Message, Thread.kol_id)
            .join(Thread, Message.thread_id == Thread.id)
            .filter(Message.direction == "inbound")
            .order_by(
                Thread.kol_id,
                Message.received_at.desc(),
                Message.id.desc(),
            )
            .all()
        )
        latest: dict[int, Message] = {}
        all_reply_kols: set[int] = set()
        for message, kol_id in source_rows:
            all_reply_kols.add(kol_id)
            if not _is_invalid_reply(message):
                latest.setdefault(kol_id, message)
        invalid_only_kols = all_reply_kols - set(latest)
        invalid_reply_rows = sorted(
            row_number
            for kol_id in invalid_only_kols
            for row_number in kol_id_rows.get(str(kol_id), [])
        )

        missing = 0
        exact = 0
        matched = 0
        difference_counts: dict[str, int] = {}
        for kol_id, message in latest.items():
            record = _build_record(message)
            found_rows = kol_id_rows.get(str(kol_id)) or email_rows.get(
                record["邮箱"], []
            )
            if not found_rows:
                missing += 1
                continue
            matched += 1
            # Ambiguous duplicates are reported separately and are not called
            # exact even if one copy happens to match.
            if len(found_rows) != 1:
                continue
            row = data_rows[found_rows[0] - 2]
            row_has_difference = False
            for column in MANAGED_COLUMNS:
                index = schema[column]
                actual = _cell_text(row[index]) if index < len(row) else ""
                expected = str(record[column])
                if actual != expected:
                    difference_counts[column] = (
                        difference_counts.get(column, 0) + 1
                    )
                    row_has_difference = True
            if not row_has_difference:
                exact += 1
        return {
            "sheet_rows": nonempty_rows,
            "database_kols": len(latest),
            "invalid_reply_kols": len(invalid_only_kols),
            "invalid_reply_rows": invalid_reply_rows,
            "matched_rows": matched,
            "missing_rows": missing,
            "exact_rows": exact,
            "duplicate_email_groups": len(duplicate_email_rows),
            "duplicate_email_rows": duplicate_email_rows,
            "duplicate_kol_id_groups": len(duplicate_kol_id_rows),
            "duplicate_kol_id_rows": duplicate_kol_id_rows,
            "managed_difference_counts": difference_counts,
        }
    finally:
        db.close()
