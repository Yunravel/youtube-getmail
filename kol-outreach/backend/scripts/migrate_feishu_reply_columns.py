"""Safely migrate the live Feishu reply columns without deleting any rows.

Old layout:
    P=CPM, Q=邮箱, R=时间戳, S=系统KOL ID, T=系统更新时间

New layout:
    P=意向, Q=邮箱, R=回信时间, S=系统KOL ID, T=CPM
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.feishu_push import FeishuClient, _cell_text


OLD_HEADERS = {
    "P": "CPM",
    "Q": "邮箱",
    "R": "时间戳",
    "S": "系统KOL ID",
    "T": "系统更新时间",
}
NEW_HEADERS = {
    "P": "意向",
    "Q": "邮箱",
    "R": "回信时间",
    "S": "系统KOL ID",
    "T": "CPM",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    client = FeishuClient()
    rows = client._get_values("A1:T10000")
    if not rows:
        raise RuntimeError("目标工作表为空，已停止迁移")
    while len(rows) > 1 and not any(_cell_text(value) for value in rows[-1]):
        rows.pop()

    headers = rows[0] + [""] * (20 - len(rows[0]))
    current = {
        column: _cell_text(headers[index])
        for column, index in zip(("P", "Q", "R", "S", "T"), range(15, 20))
    }
    if current == NEW_HEADERS:
        print({"status": "already_migrated", "rows": len(rows) - 1})
        return
    if current != OLD_HEADERS:
        raise RuntimeError(
            f"P:T 表头与预期旧结构不一致，已停止迁移: {current!r}"
        )

    last_row = len(rows)
    old_cpm = [
        [_cell_text(row[15]) if len(row) > 15 else ""]
        for row in rows[1:]
    ]
    cpm_nonempty = sum(bool(row[0]) for row in old_cpm)
    print(
        {
            "status": "ready",
            "rows": last_row - 1,
            "cpm_nonempty": cpm_nonempty,
            "apply": args.apply,
        }
    )
    if not args.apply:
        return

    # Write the new CPM copy first. If a later request fails, the original P
    # column remains intact and the script can be retried safely.
    client._put_values(
        f"T1:T{last_row}",
        [["CPM"], *old_cpm],
    )
    client._put_values(
        f"P1:P{last_row}",
        [["意向"], *([[""]] * (last_row - 1))],
    )
    client._put_values("R1:R1", [["回信时间"]])

    verify = client._get_values("A1:T10000")
    verify_headers = verify[0] + [""] * (20 - len(verify[0]))
    verified = {
        column: _cell_text(verify_headers[index])
        for column, index in zip(("P", "Q", "R", "S", "T"), range(15, 20))
    }
    copied_cpm = [
        _cell_text(row[19]) if len(row) > 19 else ""
        for row in verify[1:last_row]
    ]
    expected_cpm = [row[0] for row in old_cpm]
    if verified != NEW_HEADERS or copied_cpm != expected_cpm:
        raise RuntimeError("迁移后校验失败，请停止后续同步并人工检查")
    print(
        {
            "status": "migrated",
            "rows": last_row - 1,
            "cpm_preserved": cpm_nonempty,
        }
    )


if __name__ == "__main__":
    main()
