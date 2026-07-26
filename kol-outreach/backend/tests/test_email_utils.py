"""ensure_kol_email 的 SAVEPOINT 语义测试（DATABASE_DEVELOPMENT.md §5.1）。

核心场景：并发事务在本函数 SELECT 之后、INSERT flush 之前抢先写入了同
``(kol_id, email_normalized)`` 行，flush 撞 ``uq_kol_email_kol_normalized``
抛 IntegrityError。修复前函数只吞异常不回滚，session 停留在 pending-rollback
状态，调用方（如 api/kol.py 的 CSV 导入循环）下一次查询直接抛
PendingRollbackError，整批 500；修复后失败只回滚 SAVEPOINT 内的写入，
外层 session 必须还能继续查询、写入、提交。

并发竞争用 ``before_flush`` 事件确定性复现：事件回调在 flush 发 INSERT 前，
用同一连接裸 SQL 插入冲突行，等价于"另一个事务赢得了竞态"。
"""
import unittest

from sqlalchemy import create_engine, event, func
from sqlalchemy.orm import sessionmaker

from db import Base
from models import Kol
from models.kol_email import KolEmail
from services.email_utils import ensure_kol_email


def _inject_conflicting_row(session, kol_id: int, email: str) -> None:
    """用裸 SQL 在当前连接上插入冲突行，模拟并发事务抢先写入。

    绕过 ORM 直接走驱动，避免污染 session 的 identity map/pending 状态——
    被测函数的 SELECT 已经跑完，这行只会在它 flush INSERT 时以唯一约束
    冲突的形式暴露，与真实竞态的时序一致。
    """
    session.connection().exec_driver_sql(
        "INSERT INTO kol_email (kol_id, email, email_normalized, is_primary) "
        "VALUES (?, ?, ?, 0)",
        (kol_id, email, email),
    )


class EnsureKolEmailTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        # autoflush=False 与生产 db.SessionLocal 保持一致（api/kol.py 等
        # 调用方拿到的就是这种 session）。
        self.db = sessionmaker(bind=engine, autoflush=False)()

    def tearDown(self):
        self.db.close()

    def _create_kol(self, email: str, name: str = "kol") -> Kol:
        kol = Kol(name=name, email=email, status="pending")
        self.db.add(kol)
        self.db.commit()
        return kol

    def test_insert_and_idempotent_duplicate(self):
        """基线：插入成功；同 KOL 重复 email（含大小写/空格变体）返回已有行。"""
        kol = self._create_kol("ada@example.com")

        row = ensure_kol_email(self.db, kol.id, "Ada@Example.com", is_primary=True, source="t")
        self.assertIsNotNone(row)
        self.assertEqual(row.email_normalized, "ada@example.com")

        again = ensure_kol_email(self.db, kol.id, "  ada@example.COM ", is_primary=True)
        self.assertIs(again, row)
        self.assertEqual(self.db.query(KolEmail).count(), 1)

    def test_primary_promotion_demotes_other_rows(self):
        """提升已有行为主邮箱时，同 KOL 其他行的 is_primary 被清掉。"""
        kol = self._create_kol("a@example.com")
        ensure_kol_email(self.db, kol.id, "a@example.com", is_primary=True)
        ensure_kol_email(self.db, kol.id, "b@example.com", is_primary=False)

        promoted = ensure_kol_email(self.db, kol.id, "b@example.com", is_primary=True)
        self.assertTrue(promoted.is_primary)
        primaries = (
            self.db.query(KolEmail)
            .filter(KolEmail.kol_id == kol.id, KolEmail.is_primary.is_(True))
            .all()
        )
        self.assertEqual([r.email_normalized for r in primaries], ["b@example.com"])

    def test_integrity_error_returns_none_and_session_stays_usable(self):
        """撞唯一索引后返回 None，外层 session 必须还能查询、写入、提交。"""
        kol = self._create_kol("kol@example.com")

        def race(session, flush_context, instances):
            _inject_conflicting_row(session, kol.id, "dup@example.com")

        event.listen(self.db, "before_flush", race, once=True)
        try:
            result = ensure_kol_email(
                self.db, kol.id, "dup@example.com", is_primary=True, source="t"
            )
        finally:
            event.remove(self.db, "before_flush", race)

        self.assertIsNone(result)

        # 修复前这里就抛 PendingRollbackError——失败已回滚到 SAVEPOINT，
        # 查询必须正常，且冲突行（连同模拟的并发插入）都不残留。
        self.assertEqual(self.db.query(KolEmail).count(), 0)
        self.assertEqual(self.db.query(Kol).count(), 1)

        # 同一 session 继续写入 + 提交也必须成功。
        ok = ensure_kol_email(self.db, kol.id, "ok@example.com", is_primary=True)
        self.assertIsNotNone(ok)
        self.db.commit()
        persisted = self.db.query(KolEmail).filter(KolEmail.kol_id == kol.id).all()
        self.assertEqual([r.email_normalized for r in persisted], ["ok@example.com"])

    def test_csv_import_loop_survives_one_bad_row(self):
        """复刻 api/kol.py import_csv 循环：中间一行 email 写入失败，后续行照常导入。"""
        rows = [
            {"email": "a@example.com", "name": "A"},
            {"email": "b@example.com", "name": "B"},  # 这行会撞唯一索引
            {"email": "c@example.com", "name": "C"},
        ]

        fired = {"done": False}

        def race(session, flush_context, instances):
            # 只在 B 的 kol_email 行进入 flush 时抢先插入冲突行；
            # 循环里 db.add(kol) 后的 flush 也会触发本事件，需跳过。
            if fired["done"]:
                return
            pending = [
                o for o in session.new
                if isinstance(o, KolEmail) and o.email_normalized == "b@example.com"
            ]
            if not pending:
                return
            fired["done"] = True
            _inject_conflicting_row(session, pending[0].kol_id, "b@example.com")

        event.listen(self.db, "before_flush", race)
        imported = 0
        try:
            # 与 api/kol.py import_csv 相同的骨架：去重查询 → add → flush → ensure。
            for row in rows:
                exists = (
                    self.db.query(Kol)
                    .filter(func.lower(Kol.email) == row["email"])
                    .first()
                )
                if exists:
                    continue
                kol = Kol(name=row["name"], email=row["email"], status="pending")
                self.db.add(kol)
                self.db.flush()
                ensure_kol_email(
                    self.db, kol.id, row["email"], is_primary=True, source="csv_import"
                )
                imported += 1
            self.db.commit()
        finally:
            event.remove(self.db, "before_flush", race)

        self.assertTrue(fired["done"], "竞态注入未触发，测试未覆盖目标场景")
        # 修复前：B 行失败后 session 带毒，C 行的去重查询抛 PendingRollbackError，
        # 循环根本走不完。修复后三行 KOL 全部导入（邮箱失败不阻塞 KOL 创建），
        # 只有 B 的 kol_email 子表行缺失。
        self.assertEqual(imported, 3)
        self.assertEqual(self.db.query(Kol).count(), 3)
        self.assertEqual(
            sorted(r.email_normalized for r in self.db.query(KolEmail).all()),
            ["a@example.com", "c@example.com"],
        )


if __name__ == "__main__":
    unittest.main()
