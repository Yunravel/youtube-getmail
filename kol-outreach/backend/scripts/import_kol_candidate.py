"""导入 KOL-Find 多平台候选池到 kol_candidate 表，并把有邮箱的选入 kol + kol_email 表。

用法：
  python scripts/import_kol_candidate.py <xlsx路径> --dry-run
  python scripts/import_kol_candidate.py <xlsx路径> --commit --batch "kol-find-20260716"

数据流：
  Excel「全部候选」5103 行
    → kol_candidate（全量，UNIQUE(platform, account) 去重）
    → 有 contact_email 的行 → kol 表（邮箱去重，已存在跳过）
                            → kol_email 表（多邮箱拆分入库）

幂等：重复跑只插新行，不产生重复。
"""
from __future__ import annotations

import argparse
import io
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Union

from openpyxl import load_workbook
from sqlalchemy import func

from db import SessionLocal
from models import Kol, KolCandidate, KolEmail

# Excel 列名 → kol_candidate 字段名
COLUMN_MAP = {
    "序号": "source_row",
    "平台": "platform",
    "账号": "account",
    "主页链接": "profile_url",
    "关联YouTube账号": "related_youtube",
    "YouTube About来源": "yt_about_source",
    "发现方式": "discovery_method",
    "适配产品": "fit_product",
    "主要推荐产品": "recommend_product",
    "命中检索词数": "hit_keyword_count",
    "发现关键词/种子说明": "keyword_note",
    "抓取优先级": "crawl_priority",
    "邮箱（爬虫回填）": "email_crawler",
    "邮箱来源URL": "email_source_url",
    "邮箱核验状态": "email_verify_status",
    "国家/地区": "country_region",
    "语言": "language",
    "账号类型": "account_type",
    "粉丝数": "followers",
    "近期平均播放": "avg_views",
    "人工复核状态": "review_status",
    "备注": "remark",
    "联系邮箱": "contact_email",
    "邮箱状态": "email_status",
    "邮箱来源": "email_source",
    "其他链接": "other_links",
    "采集状态": "collect_status",
    "采集时间": "collected_at",
}

EMAIL_RE = re.compile(r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)


def as_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v).strip()


def parse_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(float(str(v).replace(",", "")))
    except (ValueError, TypeError):
        return None


def parse_emails(raw: Any) -> list[str]:
    """从联系邮箱字段拆出邮箱列表（小写、去重保序）。处理 | , ; 分隔。"""
    text = as_text(raw).replace("|", " ").replace(",", " ").replace(";", " ")
    seen: list[str] = []
    for m in EMAIL_RE.findall(text):
        e = m.strip(".,;:()[]<>\"'").lower()
        if "@" in e and e not in seen:
            seen.append(e)
    return seen


def parse_datetime(v: Any):
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v
    s = as_text(v)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def platform_normalize(raw: str) -> str:
    """规范化平台名。"""
    low = raw.lower().strip()
    return {"youtube": "YouTube", "instagram": "Instagram",
            "tiktok": "TikTok", "x": "X", "twitter": "X"}.get(low, raw.strip())


def load_rows(xlsx_source: Union[Path, bytes, io.BytesIO]) -> list[dict[str, Any]]:
    """读 Excel「全部候选」表，返回字段 dict 列表。

    xlsx_source 可以是文件路径（Path/str）、字节流（bytes）或 BytesIO，
    供 CLI 和 HTTP 上传接口共用。
    """
    # openpyxl 对裸 bytes 不直接支持，统一包成 BytesIO。
    if isinstance(xlsx_source, (bytes, bytearray)):
        xlsx_source = io.BytesIO(xlsx_source)
    wb = load_workbook(xlsx_source, read_only=True, data_only=True)
    if "全部候选" not in wb.sheetnames:
        raise ValueError(f"Excel 缺少「全部候选」表，现有: {wb.sheetnames}")
    ws = wb["全部候选"]
    headers = [as_text(c.value) for c in next(ws.iter_rows(min_row=1, max_row=1))]
    # 校验列名
    missing = [cn for cn in COLUMN_MAP if cn not in headers]
    if missing:
        raise ValueError(f"Excel 缺少列: {missing}")

    rows: list[dict[str, Any]] = []
    for values in ws.iter_rows(min_row=2, values_only=True):
        if not values or not any(v is not None for v in values):
            continue
        row = dict(zip(headers, values))
        item: dict[str, Any] = {}
        for cn, fn in COLUMN_MAP.items():
            val = row.get(cn)
            if fn in ("source_row", "hit_keyword_count"):
                item[fn] = parse_int(val)
            elif fn in ("followers", "avg_views"):
                item[fn] = parse_int(val)
            elif fn == "collected_at":
                item[fn] = parse_datetime(val)
            elif fn == "platform":
                item[fn] = platform_normalize(as_text(val))
            else:
                item[fn] = as_text(val) or None
        if not item.get("platform") or not item.get("account"):
            continue
        rows.append(item)
    wb.close()
    return rows


def run_import(
    xlsx_source: Union[Path, bytes, io.BytesIO],
    commit: bool,
    batch: str,
    db=None,
) -> dict:
    """执行导入。返回统计 dict。

    db 为 None 时内部创建独立 session（CLI 用）；传入时复用（HTTP 接口用，
    以便请求结束统一提交/回滚）。
    """
    rows = load_rows(xlsx_source)
    stats = {
        "candidate_total": len(rows),
        "candidate_inserted": 0,
        "candidate_skipped_dup": 0,
        "emailable": 0,
        "kol_inserted": 0,
        "kol_skipped_dup": 0,
        "kol_email_inserted": 0,
    }

    own_session = db is None
    if own_session:
        db = SessionLocal()
    try:
        # 预查现有 kol 邮箱（小写），避免逐行查询
        existing_kol_emails = {
            (e[0] or "").lower() for e in db.query(Kol.email).all()
        }
        # 预查现有 candidate (platform, account)
        existing_pa = {
            (p, a) for p, a in db.query(KolCandidate.platform, KolCandidate.account).all()
        }

        for item in rows:
            pa = (item["platform"], item["account"])
            # 1) kol_candidate（去重 platform+account）
            if pa in existing_pa:
                stats["candidate_skipped_dup"] += 1
            else:
                if commit:
                    db.add(KolCandidate(import_batch=batch, **item))
                existing_pa.add(pa)
                stats["candidate_inserted"] += 1

            # 2) 有邮箱 → 选入 kol + kol_email
            emails = parse_emails(item.get("contact_email"))
            if not emails:
                continue
            stats["emailable"] += 1
            primary_email = emails[0]

            if primary_email in existing_kol_emails:
                stats["kol_skipped_dup"] += 1
            else:
                if commit:
                    kol = Kol(
                        name=item["account"][:200],
                        email=primary_email[:200],
                        platform=item["platform"][:50] or None,
                        social_handle=item["account"][:200] or None,
                        profile_url=(item.get("profile_url") or "")[:500] or None,
                        channel_url=(item.get("profile_url") or "")[:500] or None,
                        country=(item.get("country_region") or "")[:50] or None,
                        niche=(item.get("recommend_product") or "")[:100] or None,
                        content_category=(item.get("recommend_product") or "")[:150] or None,
                        source=f"KOL-Find 候选池 | {batch}",
                        status="pending",
                    )
                    db.add(kol)
                    db.flush()  # 拿 kol.id
                    # kol_email：全部邮箱入库
                    for i, em in enumerate(emails):
                        db.add(KolEmail(
                            kol_id=kol.id,
                            email=em,
                            email_normalized=em,
                            is_primary=(i == 0),
                            source=f"KOL-Find 候选池 | {batch}",
                        ))
                existing_kol_emails.add(primary_email)
                stats["kol_inserted"] += 1
                stats["kol_email_inserted"] += len(emails)

        if commit:
            db.commit()
        else:
            db.rollback()
    except Exception:
        db.rollback()
        raise
    finally:
        if own_session:
            db.close()
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="导入 KOL-Find 候选池")
    parser.add_argument("source", type=Path, help="xlsx 文件路径")
    parser.add_argument("--commit", action="store_true", help="实际写库（默认 dry-run）")
    parser.add_argument("--batch", type=str, default=f"kol-find-{datetime.utcnow().strftime('%Y%m%d')}")
    args = parser.parse_args()

    mode = "写库" if args.commit else "预览(dry-run)"
    print(f"导入候选池 [{mode}] batch={args.batch}")
    print(f"读取: {args.source}")
    stats = run_import(args.source, args.commit, args.batch)
    print("\n=== 结果 ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
