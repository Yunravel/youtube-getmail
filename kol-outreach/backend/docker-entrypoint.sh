#!/bin/sh
# ============================================================
# 容器启动入口:先执行数据库迁移,再启动应用
# ------------------------------------------------------------
# 背景:db.py 的 init_db() 在非 SQLite 库上按设计跳过 create_all,
# 生产(PostgreSQL)schema 完全由 alembic 管理。此前迁移靠部署人员
# 手动执行 `alembic upgrade head`,忘跑就会在运行时报 UndefinedTable。
# 本脚本把迁移固化到容器启动流程,消除这一断链。
#
# 行为:
#   - DATABASE_URL 以 "sqlite" 开头(或未设置——config.py 会回退到
#     SQLite 默认值)→ 跳过 alembic,与旧行为完全一致,建表仍由
#     init_db() 的 create_all 完成;
#   - 其余(生产 PostgreSQL)→ 先 `alembic upgrade head`,失败则整个
#     容器启动失败(fail fast,绝不带着旧 schema 起服务);
#   - SQLite 判断与 db.py 的 _is_sqlite_url(startswith("sqlite"))
#     保持一致。
#
# 并发说明:生产仅单个 backend 容器(uvicorn --workers 1),且 compose
# 中 backend depends_on db(condition: service_healthy),迁移在 DB
# 就绪后串行执行,不存在多副本并发迁移竞争。
# ============================================================
set -eu

case "${DATABASE_URL:-}" in
  ""|sqlite*)
    echo "[entrypoint] DATABASE_URL 为 SQLite/未设置,跳过 alembic(建表走 init_db 的 create_all)"
    ;;
  *)
    echo "[entrypoint] 检测到非 SQLite 数据库,执行 alembic upgrade head ..."
    # set -e 下 alembic 非零退出会立即终止脚本 → 容器启动失败(fail fast)
    alembic upgrade head
    echo "[entrypoint] 数据库迁移完成"
    ;;
esac

# exec 用 CMD(uvicorn)替换当前 shell 进程,uvicorn 保持 PID 1,
# 正确接收 SIGTERM 等信号实现优雅停机。
exec "$@"
