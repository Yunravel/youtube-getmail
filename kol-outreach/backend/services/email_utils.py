"""邮箱规范化与 kol_email 同步的统一入口（DATABASE_DEVELOPMENT.md §5.1）。

历史背景：邮箱规范化（``strip().lower()``）和 ``kol_email`` 的写入曾散落在 5 个文件、
用 5 种不同实现，其中两个导入脚本甚至把原始邮箱直接当 ``email_normalized`` 存，
导致唯一约束 ``uq_kol_email_kol_normalized`` 可被大小写/空格变体绕过。

本模块提供：
- :func:`normalize_email`：唯一权威的纯邮箱规范化函数，所有写入 ``kol_email`` 的路径
  必须经过它生成 ``email_normalized``，不接受调用方自行提供不一致值。
- :func:`ensure_kol_email`：幂等写入/提升 ``kol_email`` 行，确保 ``kol.email`` 与
  ``kol_email`` 子表不漂移（每名 KOL 的主邮箱投影与子表主邮箱行保持一致）。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from models.kol_email import KolEmail

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def normalize_email(raw: Optional[str]) -> str:
    """邮箱规范化：``strip() + lower()``。``None`` / 空串返回 ``''``。

    这是 ``kol_email.email_normalized`` 的唯一生成方式（规范 §5.1）。空邮箱规范化后
    为空串，调用方应自行判断是否跳过写入（``kol_email.email`` 是 NOT NULL）。
    """
    return (raw or "").strip().lower()


def _is_blank(normalized: str) -> bool:
    return not normalized


def ensure_kol_email(
    db: "Session",
    kol_id: int,
    email: str,
    *,
    is_primary: bool = False,
    source: Optional[str] = None,
) -> Optional[KolEmail]:
    """幂等写入一条 ``kol_email`` 行，并在 ``is_primary=True`` 时保证主邮箱唯一。

    - ``(kol_id, email_normalized)`` 已存在：按需把该行提升为主邮箱（先清掉同 KOL
      其他行的 ``is_primary``），不重复插入、不覆盖 ``email`` 原文。
    - 不存在：插入新行；若是主邮箱，同样先清掉同 KOL 其他行的 ``is_primary``。

    返回受影响的 :class:`KolEmail`，空邮箱或异常时返回 ``None``（不抛异常，由调用方
    决定是否中断业务；邮箱规范化失败不应阻塞 KOL 本身的创建）。

    整段读写包在 SAVEPOINT（``db.begin_nested()``）里：``flush`` 失败（典型如并发
    写入撞 ``uq_kol_email_kol_normalized`` 或 ``lower(trim(email))`` 唯一索引抛
    IntegrityError）只回滚本函数内的写入，外层 session 保持可用。若只吞异常不设
    SAVEPOINT，session 会停留在 pending-rollback 状态，调用方（如 CSV 导入循环）
    下一次用同一 session 就抛 PendingRollbackError——"不阻塞调用方"的契约名存实亡。

    主邮箱唯一性目前仅在代码层保证（规范 §3.2 列为“未由数据库强制的约束”），
    未加 ``CHECK`` / 部分唯一索引，待业务确认语义后再单独 migration。
    """
    normalized = normalize_email(email)
    if _is_blank(normalized):
        return None

    try:
        with db.begin_nested():
            existing = (
                db.query(KolEmail)
                .filter(
                    KolEmail.kol_id == kol_id,
                    KolEmail.email_normalized == normalized,
                )
                .first()
            )

            if existing is not None:
                if is_primary and not existing.is_primary:
                    # 提升为主邮箱前，清掉同 KOL 其他主邮箱行（代码层保证唯一）。
                    db.query(KolEmail).filter(
                        KolEmail.kol_id == kol_id,
                        KolEmail.is_primary.is_(True),
                        KolEmail.id != existing.id,
                    ).update({KolEmail.is_primary: False}, synchronize_session=False)
                    existing.is_primary = True
                    db.flush()
                return existing

            if is_primary:
                db.query(KolEmail).filter(
                    KolEmail.kol_id == kol_id,
                    KolEmail.is_primary.is_(True),
                ).update({KolEmail.is_primary: False}, synchronize_session=False)

            row = KolEmail(
                kol_id=kol_id,
                email=email.strip(),
                email_normalized=normalized,
                is_primary=is_primary,
                source=source,
            )
            db.add(row)
            db.flush()
            return row
    except Exception as exc:  # noqa: BLE001 - 邮箱同步失败不应阻塞 KOL 创建
        logger.warning("ensure_kol_email 失败 kol_id=%s email=%r: %s", kol_id, email, exc)
        return None


def find_kol_by_any_email(db: "Session", email: Optional[str]) -> Optional["Kol"]:
    """按邮箱定位 KOL：先查主表 ``kol.email``，再查 ``kol_email`` 别名表。

    回信建档前必须用本函数而不是裸查 ``kol.email``——达人换邮箱/经纪人代回时，
    新地址往往已作为别名挂在原 KOL 下；漏查别名表就会为同一达人重复建裸档
    （2026-07-27 排查：Abhishek hey@/hi@ 双档即此因，见 kol 2415→172 归并）。
    别名命中多个 KOL 时优先主邮箱行、再取最早建档的，保证结果确定。
    """
    from sqlalchemy import func

    from models.kol import Kol

    normalized = normalize_email(email)
    if _is_blank(normalized):
        return None
    kol = db.query(Kol).filter(func.lower(Kol.email) == normalized).first()
    if kol is not None:
        return kol
    alias = (
        db.query(KolEmail)
        .filter(KolEmail.email_normalized == normalized)
        .order_by(KolEmail.is_primary.desc(), KolEmail.kol_id.asc())
        .first()
    )
    if alias is not None:
        return db.get(Kol, alias.kol_id)
    return None
