"""导入器共享的解析工具。

抽取自 import_kol_candidate.py 与 import_email_collection.py 的重复实现，
供两个 Excel 导入器与 services/crawler/ 采集器共用，避免三处分叉。

设计原则：
- parse_int 统一采用支持 K/M 后缀的版本（更通用；原 import_kol_candidate 的纯数字
  版本是其子集，行为对纯数字输入一致）。
- 其余函数（as_text / parse_emails / parse_datetime / platform_normalize / _trunc）
  与原实现逐行等价，迁移后行为不变。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

# 邮箱正则：与原导入器一致（提取候选后再做清洗）
EMAIL_RE = re.compile(r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)


def as_text(v: Any) -> str:
    """任意值转清洗后的字符串。None→""，datetime→isoformat，其余 str().strip()。"""
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v).strip()


def _trunc(v: str | None, max_len: int) -> str | None:
    """截断字符串到 max_len，避免超 varchar 宽度。None 透传。"""
    if v is None:
        return None
    return v[:max_len] if len(v) > max_len else v


def parse_int(v: Any) -> int | None:
    """解析粉丝数/浏览量。

    支持纯数字、千分位逗号、K/M 后缀。负数截到 0。失败返回 None。
    （统一版：原 import_email_collection 的实现，对纯数字输入与原
    import_kol_candidate 行为一致。）
    """
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return max(0, int(v))
    s = str(v).lower().replace(",", "").replace(" ", "").strip()
    mult = 1
    if s.endswith("k"):
        s, mult = s[:-1], 1_000
    elif s.endswith("m"):
        s, mult = s[:-1], 1_000_000
    try:
        return max(0, int(float(s) * mult))
    except ValueError:
        return None


def parse_emails(raw: Any) -> list[str]:
    """从联系邮箱字段拆出邮箱列表。

    处理 | , ; 分隔符；小写、去重、保序；剥离首尾标点。
    """
    text = as_text(raw).replace("|", " ").replace(",", " ").replace(";", " ")
    seen: list[str] = []
    for m in EMAIL_RE.findall(text):
        e = m.strip(".,;:()[]<>\"'").lower()
        if "@" in e and e not in seen:
            seen.append(e)
    return seen


def parse_datetime(v: Any):
    """解析采集时间。支持 YYYY-MM-DD[ HH:MM[:SS]] 与 datetime 透传。失败返回 None。"""
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
    """规范化平台名。大小写不敏感；twitter/twitter-x 都归 X。"""
    low = (raw or "").lower().strip()
    return {
        "youtube": "YouTube",
        "instagram": "Instagram",
        "tiktok": "TikTok",
        "x": "X",
        "twitter": "X",
        "twitter/x": "X",
    }.get(low, (raw or "").strip())
