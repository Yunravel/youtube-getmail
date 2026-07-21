# 工作站迁移包内容

打包时间：2026-07-19（Asia/Shanghai）

本迁移包包含：

- 当前工作区的完整前端、后端、数据库迁移、测试及文档源码，包括尚未提交到 Git 的最新修改。
- 真实 `.env.prod` 与 `backend/.env` 开发配置。
- PostgreSQL 16 全量备份 `database/kol_outreach.dump`。
- Windows 工作站专用 `docker-compose.workstation.yml`。
- 自动安装、数据库恢复、日常启停和备份脚本。
- 中文部署说明 `WORKSTATION_DEPLOY.md`。

数据库快照校验结果：

- KOL：860 行
- 会话：37 行
- 消息：39 行
- PostgreSQL archive：192 个 TOC 对象
- 已在全新 PostgreSQL 16 容器中完成恢复演练，行数一致。

为避免跨平台依赖污染，ZIP 不包含可重建的 `frontend/node_modules`、`frontend/dist`、Python `__pycache__`、运行日志和 Git 元数据。依赖版本由 `package-lock.json`、`requirements.txt` 和 Dockerfile 重建；前后端 Docker 镜像已经实际构建验证通过。

首次使用请直接阅读 [WORKSTATION_DEPLOY.md](./WORKSTATION_DEPLOY.md)，不要运行服务器/NAS 部署文件。
