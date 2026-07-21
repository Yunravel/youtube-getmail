# Windows 工作电脑迁移部署指南

本包用于把当前 KOL Outreach 项目完整迁移到另一台 Windows 工作电脑。它包含前端、后端、真实开发配置和 PostgreSQL 全量数据库备份，不使用服务器域名、Traefik 或 HTTPS。

> **高度敏感：** 包内 `.env.prod`、`backend/.env`、`database/kol_outreach.dump` 含真实密钥、账号配置、联系人和邮件数据。只能通过加密磁盘或可信局域网传输；部署完成后不要上传网盘、GitHub 或聊天工具。

## 1. 目标电脑要求

- Windows 10/11 64 位。
- Docker Desktop，安装时启用 WSL 2 后端。
- 至少 4 GB 可用内存、10 GB 可用磁盘。
- 能访问 Docker Hub、npm 和 Python 软件源以首次构建镜像。
- 本机端口 `5432`、`8000`、`8080` 未被占用。

不需要单独安装 Python、Node.js 或 PostgreSQL，它们都在 Docker 容器中运行。

## 2. 首次部署

1. 将 ZIP 完整复制到目标电脑并解压，例如 `D:\KOL\kol-outreach-workstation`。不要只复制子目录。
2. 启动 Docker Desktop，等待左下角显示 Engine running。
3. 在解压目录空白处按住 Shift 右键，打开 PowerShell。
4. 执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\tools\install-workstation.ps1
```

脚本会依次完成：启动 PostgreSQL、恢复 `database/kol_outreach.dump`、构建后端、构建前端、启动全部容器、检查健康状态和显示数据库 KOL 行数。

完成后访问：

- 工作台：<http://localhost:8080>
- 后端 API 文档：<http://localhost:8000/docs>
- 后端健康检查：<http://localhost:8080/health>

## 3. 日常启动与停止

- 双击 `tools\start-workstation.cmd` 启动并打开工作台。
- 双击 `tools\stop-workstation.cmd` 停止应用。
- Docker Desktop 设置中可以关闭“开机自动启动”；需要使用项目时再启动 Docker Desktop。

停止容器不会删除数据库。不要执行 `docker compose down -v`，其中 `-v` 会永久删除数据库卷。

## 4. 数据库与备份

首次部署恢复的是打包时刻的完整 PostgreSQL 数据。数据库保存在 Docker 卷 `kol-workstation-db-data` 中，而不是项目目录。

手动备份：

```powershell
.\tools\backup-workstation.ps1
```

备份会写到 `database\manual-backups\kol_outreach-日期时间.dump`。建议每次大量导入或升级前备份，并将备份复制到另一块加密磁盘。

若目标电脑已有此项目数据，安装脚本会拒绝覆盖。确认要用随包数据库覆盖时执行：

```powershell
.\tools\install-workstation.ps1 -ForceRestore
```

## 5. 更新代码但保留目标电脑数据库

用新版本项目文件覆盖源码后，不要重新恢复旧数据库，执行：

```powershell
.\tools\install-workstation.ps1 -SkipRestore
```

这会重新构建前后端，并保留 Docker 卷中的现有数据库。

## 6. 配置说明

- `.env.prod`：Docker Compose 使用的真实 Snov、OpenAI、数据库密码和前端配置。
- `backend/.env`：保留当前开发环境配置；容器内的数据库地址由 Compose 自动改为 `db:5432`。
- `docker-compose.workstation.yml`：仅监听本机回环地址，局域网其他电脑默认无法访问。
- KOL 勾选推送 Snov、Snov 定时同步和 AI 功能继续使用包内真实凭据。

如果修改 `.env.prod`，需要重新构建：

```powershell
docker compose -f docker-compose.workstation.yml --env-file .env.prod up -d --build
```

## 7. 工作电脑部署的限制

- 本机关闭、休眠或 Docker Desktop 停止时，Snov 定时同步不会运行。
- Snov 无法从公网直接调用 `localhost` webhook。现有两分钟轮询可以补拉普通回信；若必须实时接收 webhook，需要另配安全隧道或公网服务器。
- 当前看板免登录，仅绑定 `127.0.0.1`。不要擅自改成 `0.0.0.0` 暴露到办公网或公网。

## 8. 故障排查

查看容器状态：

```powershell
docker compose -f docker-compose.workstation.yml --env-file .env.prod ps
```

查看日志：

```powershell
docker compose -f docker-compose.workstation.yml --env-file .env.prod logs --tail 200
```

常见问题：

- `port is already allocated`：关闭占用 5432/8000/8080 的程序，或在 `.env.prod` 增加 `POSTGRES_PORT`、`BACKEND_PORT`、`FRONTEND_PORT` 后重试。
- Docker 构建下载失败：确认代理允许 Docker Desktop 访问 Docker Hub、npm 和 PyPI。
- 页面打开但 API 失败：检查 `kol-workstation-backend` 和 `kol-workstation-db` 日志。
- Snov/OpenAI 失败：确认目标电脑网络可访问对应 API，且包内密钥仍有效。

## 9. 完整卸载

先备份数据库，然后执行：

```powershell
docker compose -f docker-compose.workstation.yml --env-file .env.prod down
```

这只删除容器和网络，保留数据库卷。只有确认不再需要任何数据时，才可手工删除 `kol-workstation-db-data` 卷。
