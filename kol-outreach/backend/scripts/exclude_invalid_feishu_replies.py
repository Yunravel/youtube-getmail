"""Remove automatic-only replies from the final Feishu view without deleting rows."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import SessionLocal
from models import Kol, Message, Thread
from services.feishu_push import (
    COLUMNS,
    FeishuClient,
    OPERATOR_COLUMNS,
    _cell_email,
    _cell_text,
    _is_invalid_reply,
    _normalize_header,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    client = FeishuClient()
    rows = client._get_values("A1:T10000")
    while len(rows) > 1 and not any(_cell_text(value) for value in rows[-1]):
        rows.pop()
    if not rows:
        raise RuntimeError("目标工作表为空")

    schema = {
        _normalize_header(_cell_text(value)): index
        for index, value in enumerate(rows[0])
        if _normalize_header(_cell_text(value)) in COLUMNS
    }
    missing = set(COLUMNS) - set(schema)
    if missing:
        raise RuntimeError(f"飞书表头不完整，已停止: {sorted(missing)!r}")

    db = SessionLocal()
    try:
        inbound = (
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
        any_by_kol: dict[int, Message] = {}
        valid_kols: set[int] = set()
        for message, kol_id in inbound:
            any_by_kol.setdefault(kol_id, message)
            if not _is_invalid_reply(message):
                valid_kols.add(kol_id)
        invalid_kols = set(any_by_kol) - valid_kols
        emails = {
            kol.id: str(kol.email or "").strip().lower()
            for kol in db.query(Kol).filter(Kol.id.in_(invalid_kols)).all()
        }
    finally:
        db.close()

    id_index = schema[COLUMNS[18]]
    email_index = schema[COLUMNS[16]]
    matches: dict[int, list[int]] = {kol_id: [] for kol_id in invalid_kols}
    for row_number, row in enumerate(rows[1:], start=2):
        row_kol_id = _cell_text(row[id_index]) if id_index < len(row) else ""
        row_email = (
            _cell_email(row[email_index]).lower()
            if email_index < len(row)
            else ""
        )
        for kol_id in invalid_kols:
            if row_kol_id == str(kol_id) or (
                not row_kol_id and emails.get(kol_id) == row_email
            ):
                matches[kol_id].append(row_number)

    # An invalid-only KOL can have multiple legacy rows with the same email.
    # All such rows belong to the same excluded final-view identity, while the
    # authoritative messages remain preserved in the database.  No match is
    # also valid: that KOL has already been removed from the final view.
    target_rows = sorted(
        {
            row_number
            for row_numbers in matches.values()
            for row_number in row_numbers
        }
    )
    operator_indexes = [schema[column] for column in OPERATOR_COLUMNS]
    protected = []
    for row_number in target_rows:
        row = rows[row_number - 1]
        if any(
            index < len(row) and _cell_text(row[index])
            for index in operator_indexes
        ):
            protected.append(row_number)
    if protected:
        raise RuntimeError(
            f"自动回复行含人工维护字段，已停止且未写入: {protected!r}"
        )

    print(
        {
            "invalid_kols": len(invalid_kols),
            "already_excluded_kols": sum(
                not row_numbers for row_numbers in matches.values()
            ),
            "target_rows": target_rows,
            "operator_cells_present": False,
            "apply": args.apply,
        }
    )
    if not args.apply:
        return

    blank_row = [""] * len(COLUMNS)
    for row_number in target_rows:
        client._put_values(f"A{row_number}:T{row_number}", [blank_row])

    verify = client._get_values("A1:T10000")
    uncleared = [
        row_number
        for row_number in target_rows
        if row_number - 1 < len(verify)
        and any(_cell_text(value) for value in verify[row_number - 1][:20])
    ]
    if uncleared:
        raise RuntimeError(f"自动回复行清空后校验失败: {uncleared!r}")
    print({"status": "excluded", "cleared_rows": target_rows})


if __name__ == "__main__":
    main()
