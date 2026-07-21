# IMAP 附件同步方案（第二层）

## 目标
绕开 Snov（它不传附件），用 IMAP 直连你 41 个 `gmail-smtp` 发信邮箱，把 KOL 回信里的**真附件**抓下来存到中台本地磁盘，前端可下载。覆盖定时轮询（未读）+ 手动触发（限定时间范围）两种模式。

## 已确认的全部决策

| 维度 | 决策 |
|---|---|
| 触发方式 | 定时轮询（每 10 分钟，只查未读）+ 前端手动按钮（限定时间范围） |
| 凭据存储 | 数据库表 `mailbox_credential` + **Fernet 加密** + 前端管理页 |
| 附件存储 | 本地磁盘 `backend/data/attachments/<message_id>/<filename>` |
| 网络通道 | PySocks 走 SOCKS5 代理 `127.0.0.1:7897`（代码层兜底，不依赖 Clash 全局模式） |
| 邮箱覆盖 | 41 个 `gmail-smtp`（5 个 `dfy-google` 放弃，凭据不在手） |
| 邮件→thread 匹配 | `from_email + subject 模糊 + 时间窗口 ±3 天` |
| 密码加密 | Fernet 对称加密，主密钥 `ATTACHMENT_MASTER_KEY` 从 .env 读 |

## 实施清单（按依赖顺序）

### 阶段 1：基础设施

#### 1.1 依赖
- `requirements.txt` 加 `PySocks>=1.7.1`（SOCKS 代理）和 `cryptography>=41.0`（Fernet）。pip 需走代理安装（Clash 全局模式已开）。

#### 1.2 配置项（`backend/config.py`）
新增 settings：
- `IMAP_PROXY_HOST`（默认 `127.0.0.1`）
- `IMAP_PROXY_PORT`（默认 `7897`）
- `IMAP_PROXY_ENABLED`（默认 `true`）
- `ATTACHMENT_MASTER_KEY`（Fernet 密钥，必填，缺失时降级为明文并打警告日志）
- `ATTACHMENT_STORAGE_DIR`（默认 `./data/attachments`）
- `IMAP_SYNC_ENABLED`（默认 `true`）
- `IMAP_SYNC_INTERVAL_SECONDS`（默认 `600`，即 10 分钟）
- `.env.prod.example` 同步更新这些项 + `.gitignore` 加 `backend/data/`

#### 1.3 数据模型 + 迁移
- **新模型** `backend/models/mailbox_credential.py`（仿 `crawler_product.py`）：
  ```python
  class MailboxCredential(Base):
      __tablename__ = "mailbox_credential"
      id, email (unique, indexed), encrypted_password (Text),
      provider (默认 "gmail"), imap_host (默认 "imap.gmail.com"),
      imap_port (默认 993), last_synced_at (DateTime, nullable),
      last_sync_status (String, nullable), last_error (Text, nullable),
      enabled (Boolean, 默认 True), created_at, updated_at
  ```
- 注册到 `backend/models/__init__.py` 的 import + `__all__`。
- **新迁移** `backend/alembic/versions/20260721_0004_mailbox_credential.py`：
  - `revision="mailbox_cred_0004"`，`down_revision="crawler_product_0003"`
  - `upgrade()` 用 `if table not in inspector.get_table_names()` 幂等守卫（仿 crawler_product 迁移）
  - `downgrade()` drop table

#### 1.4 加密工具 `backend/services/crypto.py`（新文件）
- `encrypt_password(plain: str) -> str` / `decrypt_password(token: str) -> str`
- 用 `cryptography.fernet.Fernet`，密钥从 `settings.ATTACHMENT_MASTER_KEY` 读
- 密钥缺失时：打 warning 日志，降级为 base64 存储（不加密），便于 dev 起步；prod 必须配

### 阶段 2：IMAP 同步核心

#### 2.1 `backend/services/imap_client.py`（新文件，核心）
- `class ImapMailbox`：封装单个邮箱的 IMAP 操作
  - `__init__(email, password, proxy_settings)`：存配置
  - `_connect() -> imaplib.IMAP4_SSL`：用 PySocks 的 `socks.create_connection` 包装 socket，走 SOCKS5 代理建 TLS 连接到 `imap.gmail.com:993`；代理禁用时直连
  - `fetch_unread(since_days=None) -> list[EmailMessage]`：搜未读（`UNSEEN`），可选加 `SINCE` 日期过滤，逐封 `BODY.PEEK[]` 拉取，解析成结构化对象（from/to/subject/date/message_id/attachments）
  - `mark_read(uid)`：同步成功后标记已读，避免重复抓
  - 附件解析：遍历 `msg.walk()`，`Content-Disposition: attachment` 的 part 解码 base64 得到 bytes + filename + content_type + size
- 上下文管理器 `with ImapMailbox(...) as mb:` 自动连接和登出
- 所有网络异常捕获，抛出带邮箱名的清晰错误

#### 2.2 `backend/services/attachment_sync.py`（新文件，业务编排）
- `sync_one_mailbox(credential, mode, since_days, on_progress) -> dict`：
  - 连邮箱 → 拉邮件（mode="unread" 查 UNSEEN；mode="manual" 查 SINCE since_days）
  - 对每封邮件：用 `from_email + subject 去前缀 + ±3天` 在 DB 找匹配的 inbound `message`
  - 找到匹配：
    - 邮件有附件 → 存磁盘 `data/attachments/<message_id>/<safe_filename>`，把元数据 `merge_attachments` 进 `message.attachments`（加 `local_path` 字段）
    - 无附件 → 跳过
  - 找不到匹配 → 记日志（KOL 邮箱不在中台 / 跨 campaign），不报错
  - 成功处理的邮件标已读（仅 unread 模式；manual 模式不动已读状态，避免误标）
  - 返回统计 `{fetched, matched, attached, skipped, errors}`
- `sync_all_mailboxes(mode, since_days, on_progress) -> dict`：遍历所有 enabled 的凭据，聚合结果
- 文件名安全化：`_safe_filename()` 去掉路径分隔符、控制字符，防目录穿越

### 阶段 3：调度 + API

#### 3.1 `backend/services/attachment_scheduler.py`（新文件，仿 `snov_scheduler.py`）
- 单例 `BackgroundScheduler`，job 调 `sync_all_mailboxes(mode="unread")`
- `start_attachment_scheduler()` / `stop_attachment_scheduler()`
- 间隔 `settings.IMAP_SYNC_INTERVAL_SECONDS`，`max_instances=1, coalesce=True`
- 在 `backend/main.py` 的 lifespan 里 start/stop

#### 3.2 `backend/api/mailbox_credentials.py`（新文件，CRUD 路由）
- 仿 `backend/api/operators.py` 模式
- `GET /mailbox-credentials` → 列出所有（**密码字段不返回，只返回 `has_password: bool`**）
- `POST /mailbox-credentials` → 创建（接收明文密码，加密存）
- `PUT /mailbox-credentials/{id}` → 更新（密码可选，不传不动）
- `DELETE /mailbox-credentials/{id}`
- `POST /mailbox-credentials/import-from-snov` → 调 `/v2/sender-accounts/emails`，把 `gmail-smtp` 的邮箱批量导入（密码留空，提示用户去填）—— 这解决你"从 Snov 导入"的诉求，省手动录 41 个邮箱地址
- 注册到 `backend/api/__init__.py`

#### 3.3 `backend/api/attachments.py`（新文件，同步触发 + 下载）
- `POST /attachments/sync` → 手动触发，body 接 `{since_days: int}`（默认 30，0 表示全部），后台异步跑（仿 `api/threads.py:22-45` 的 `_backfill_jobs` 模式），返回 `job_id`
- `GET /attachments/sync/status` → 查手动任务进度
- `GET /attachments/download/{message_id}/{filename}` → 读本地文件，`StreamingResponse` 返回下载（仿 `api/threads.py:192-217`），带 `Content-Disposition`，校验 message_id 防目录穿越
- 注册到 `backend/api/__init__.py`

### 阶段 4：前端

#### 4.1 API 客户端（`frontend/src/api/index.js`）
新增两个 api 对象（仿 `crawlerApi`）：
- `mailboxCredentialApi`：list / create / update / remove / importFromSnov
- `attachmentApi`：sync(sinceDays) / syncStatus / downloadUrl(messageId, filename)

#### 4.2 新页面 `frontend/src/views/MailboxSettings.vue`（凭据管理，仿 Crawler.vue 的 CRUD）
- 路由 `/mailbox-settings`，菜单项"邮箱配置"（MailOutlined 图标）
- 表格列：邮箱 / provider / IMAP 状态 / 最后同步 / 已启用 / 操作
- 顶部"从 Snov 导入"按钮 → 调 importFromSnov，提示用户去填密码
- 新增/编辑弹窗：邮箱、应用专用密码（编辑时留空表示不改）、provider、是否启用
- 删除二次确认

#### 4.3 `ThreadDetail.vue` 附件区增强
- 现有附件区（112-128 行）已有 `<a :href="attachment.url">` 逻辑
- 加判断：`attachment.local_path` 存在时，链接指向 `/api/attachments/download/{message_id}/{filename}` 而非外链
- 文案区分：真附件显示文件名+大小，网盘链接显示"Google Drive"

#### 4.4 `HotLeads.vue` 或 `Mailbox.vue` 加"同步附件"按钮（手动触发）
- 按钮 + 时间范围下拉（7天/30天/90天/全部）→ 调 `attachmentApi.sync`
- 轮询 `syncStatus` 显示进度（仿 crawler 的轮询模式）

### 阶段 5：测试 + 验证

#### 5.1 单测 `backend/tests/test_attachment_sync.py`（新文件）
- `imap_client`：用 `unittest.mock` 模拟 imaplib，测 fetch_unread / 附件解析 / 代理 socket 包装逻辑
- `attachment_sync`：用 sqlite 内存库 + mock ImapMailbox，测：
  - 邮件→thread 匹配（from_email+subject+时间窗口）
  - 有附件→存盘+回填 message.attachments
  - 无附件→跳过
  - 无匹配 thread→记日志不报错
  - 文件名安全化（防 `../../etc/passwd`）
- `crypto`：加密→解密 round-trip，密钥缺失降级
- mailbox_credentials API：CRUD + 密码不回传

#### 5.2 端到端验证
1. `pip install` PySocks/cryptography（走代理）
2. `alembic upgrade head` 建表
3. 在 MailboxSettings 页面"从 Snov 导入"，然后填 `honrath6791184@gmail.com` 的密码（你已有）
4. 点"同步附件"，限定 30 天 → 应抓到前面验证时看到的 Daniel Davidson 的 3 个 PDF
5. 打开对应 thread，附件区出现可下载的 PDF 链接

## 不做的事（边界）
- ❌ 不碰 5 个 `dfy-google` 邮箱（凭据不在手）
- ❌ 不做邮件正文同步（只抓附件，正文仍由 Snov webhook 负责）
- ❌ 不做附件内容解析/OCR（只下载存储）
- ❌ 不动 Snov webhook 链路（第一层网盘链接提取保留，两层共存）
- ❌ 不做 OAuth（应用专用密码够用，OAuth 对 41 个邮箱运维成本更高）

## 风险与缓解
- **代理不稳定**：每次同步捕获异常，记录 `last_error` 到凭据表，下次重试，单邮箱失败不影响其他
- **邮件匹配不上**：用宽松匹配（subject 去掉 Re:/Fwd: 前缀 + ±3 天窗口），匹配失败的记日志供排查，不阻塞
- **密码泄露**：DB 加密存储 + API 不回传密码 + 下载端点校验 message_id 白名单
- **磁盘膨胀**：附件按 message_id 分目录，后续可加清理脚本（本期不做）

## 实施顺序建议
分两批交付，避免一次性改动过大：
- **第一批**（本次）：阶段 1+2+3（基础设施 + IMAP 同步 + API）+ 后端单测，先让后端能跑通"手动同步并抓到附件"
- **第二批**（紧接着）：阶段 4 前端 + 端到端验证

你确认方案后我就从第一批开始写。