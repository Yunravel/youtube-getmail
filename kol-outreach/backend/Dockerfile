FROM python:3.11-slim

# ============================================================
# 构建参数:注入 Git Commit SHA,用于线上版本追溯
# ------------------------------------------------------------
# 使用方式(三选一即可):
#   1) 在 .env.prod 填: GIT_SHA=$(git rev-parse HEAD)
#   2) 部署时临时:   docker build --build-arg GIT_SHA=$(git rev-parse HEAD) ./backend
#   3) 不传:         会落到下面的 default 值 "unknown"
#
# 这样改的目的:线上出问题时, `docker inspect` 或 `docker exec cat /app/GIT_SHA`
# 能直接拿到当前容器对应的 commit,而不是靠"猜 mtime"。
# ============================================================
ARG GIT_SHA=unknown

WORKDIR /app

# 系统依赖(PostgreSQL 驱动需要)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 把 SHA 写到容器内,应用启动时读取 + 运维可 docker exec 直查
#   - 文件存在性: build 阶段必生成,即使 ARG 没传,也会写入 "unknown"
#   - 权限:       644,任何用户可读(无敏感信息)
RUN echo "${GIT_SHA}" > /app/GIT_SHA

# OCI 镜像元数据标准 —— `docker inspect` 直接拿得到
# 加上 source / created,后续接 Harbor / GHCR 时不用再改 Dockerfile
LABEL org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.source="https://github.com/richup/kol-outreach" \
      org.opencontainers.image.created=""

EXPOSE 8000

# 健康检查(容器自检)
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 建表仍使用 create_all；单 worker 避免首次启动时两个 worker 并发建表。
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
