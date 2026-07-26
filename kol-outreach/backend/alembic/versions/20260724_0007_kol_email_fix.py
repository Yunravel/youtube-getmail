"""fix kol_email drift: backfill missing rows + normalize email_normalized

Revision ID: kol_email_fix_0007
Revises: feishu_sync_0006
Create Date: 2026-07-24

背景（DATABASE_DEVELOPMENT.md §5.1、§12）：
``kol.email`` 是兼容性主邮箱投影，``kol_email`` 保存一对多邮箱。历史上有 4 个写入
路径（Snov 同步、webhook 自动建 KOL、CSV 导入、legacy 脚本）只写 ``kol.email``、
不写 ``kol_email`` 子表，导致 ``kol.email`` 有值但 ``kol_email`` 无对应行的漂移
（生产库实测 236 行）。``kol_v2_0001`` 的 ``upgrade_backfill`` 只回填了有
``contact_notes`` 的 KOL，Snov/webhook/CSV 创建的 KOL（无 contact_notes）被跳过，
正是这 236 行的来源。

同时，两个导入脚本历史上把原始邮箱字符串直接当 ``email_normalized`` 存（虽
``parse_emails`` 已隐式 lower，但字面未走统一 helper），本 migration 顺带把全表
``email_normalized`` 校正为 ``lower(trim(email))``，与唯一约束语义对齐。

本 migration 是纯数据修复，不改 schema（不加列、不加约束、不改类型）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "kol_email_fix_0007"
down_revision: Union[str, None] = "feishu_sync_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 回填行的来源标记，用于 downgrade 精确回滚（只删本次插入的行，不动历史数据）。
BACKFILL_SOURCE = "kol_email_fix_0007_backfill"


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # Step A — 前置重复检查（规范 §4.3、§7.4、§10）
    # 规范化后若同一 (kol_id, email_normalized) 已存在多行，说明历史脏数据会让
    # 后续 UPDATE 撞唯一约束。此时不得静默删行，必须中止并打印冲突清单，人工确认。
    # ------------------------------------------------------------------
    dup_rows = bind.execute(
        sa.text(
            "SELECT kol_id, lower(trim(email_normalized)) AS norm, count(*) AS c "
            "FROM kol_email "
            "GROUP BY kol_id, lower(trim(email_normalized)) "
            "HAVING count(*) > 1"
        )
    ).fetchall()
    if dup_rows:
        conflict_list = ", ".join(
            f"(kol_id={r[0]}, norm={r[1]!r}, count={r[2]})" for r in dup_rows[:20]
        )
        raise RuntimeError(
            f"kol_email 存在 {len(dup_rows)} 组 (kol_id, email_normalized) 重复，"
            f"中止迁移以避免破坏唯一约束。冲突清单（前20）: {conflict_list}。"
            "请人工确认合并/保留策略后重跑。禁止自动删行（DATABASE_DEVELOPMENT.md §10）。"
        )

    # ------------------------------------------------------------------
    # Step B — 回填缺失的 kol_email 行（核心修复）
    # 对每个 kol.email 非空、但 kol_email 无任何行的 KOL，插入主邮箱行。
    # NOT EXISTS 保证幂等：重跑只补缺失行。
    # ------------------------------------------------------------------
    backfill_result = bind.execute(
        sa.text(
            "INSERT INTO kol_email (kol_id, email, email_normalized, is_primary, source, created_at) "
            "SELECT k.id, k.email, lower(trim(k.email)), true, :src, CURRENT_TIMESTAMP "
            "FROM kol k "
            "WHERE k.email IS NOT NULL AND trim(k.email) <> '' "
            "  AND NOT EXISTS (SELECT 1 FROM kol_email e WHERE e.kol_id = k.id)"
        ),
        {"src": BACKFILL_SOURCE},
    )
    backfilled = backfill_result.rowcount or 0

    # ------------------------------------------------------------------
    # Step C — 校正历史 email_normalized 为 lower(trim(email))
    # 修两个导入脚本“字面未走统一 helper”的后遗症，使全表与唯一约束语义一致。
    # 若此处触发唯一约束冲突，说明 Step A 之后仍有跨行（不同 kol_id 间无冲突、
    # 但同一 kol_id 内 email 原文不同而规范化后撞车）的隐患——同样中止，人工处理。
    # ------------------------------------------------------------------
    norm_result = bind.execute(
        sa.text(
            "UPDATE kol_email "
            "SET email_normalized = lower(trim(email)) "
            "WHERE email_normalized <> lower(trim(email))"
        )
    )
    normalized = norm_result.rowcount or 0

    print(
        f"[kol_email_fix_0007] 回填缺失行: {backfilled}, "
        f"规范化校正: {normalized}"
    )


def downgrade() -> None:
    bind = op.get_bind()

    # 删除本次回填插入的行（按 source 精确匹配，不动历史数据）。
    removed = bind.execute(
        sa.text("DELETE FROM kol_email WHERE source = :src"),
        {"src": BACKFILL_SOURCE},
    ).rowcount or 0

    # 注意：Step C 的 email_normalized 规范化更新不可精确逆向——原始大小写/空格
    # 已在 UPDATE 时丢失。如需完全回滚到迁移前状态，请从备份恢复
    # （database/manual-backups/pre_kol_email_fix_*.dump），不要靠本 downgrade 猜测。
    print(
        f"[kol_email_fix_0007] downgrade: 删除回填行 {removed}。"
        "注意 email_normalized 规范化不可逆，完整回滚需从备份恢复。"
    )
