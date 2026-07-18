# KOL 外联中台 · 开发维护手册

> 本文是面向**项目维护者**的架构手册。配合下列文档阅读：
> - [`README.md`](README.md) —— 产品介绍、跑起来、CSV 导入格式、API 速览
> - [`docs/PROJECT_SPEC.md`](docs/PROJECT_SPEC.md) —— 正式需求规格（RFC 风格 必须/应当/可以）
> - [`docs/DATA_CONSTRAINTS.md`](docs/DATA_CONSTRAINTS.md) —— 三大数据来源的字段级约束
> - [`docs/PIPPIT_KOL_DATA_STANDARDIZATION_DESIGN.md`](docs/PIPPIT_KOL_DATA_STANDARDIZATION_DESIGN.md) —— Tier 1 数据标准化设计
> - [`docs/NAS_DEPLOY.md`](docs/NAS_DEPLOY.md) / [`docs/PREREQUISITES.md`](docs/PREREQUISITES.md) —— 部署与前置准备

## 命名约定

为避免与具体厂商耦合，本文对外部邮件系统统一使用以下流程化称呼：

| 本文用语 | 指代 |
|---|---|
| **邮件平台** | 实际承担发信 / 收信 / 联系人管理 / 营销活动的外部邮件系统 |
| **邮件平台 webhook** | 该系统在邮件发出或收到回信时回调本平台的入口 |
| **邮件平台定时同步** | 本平台主动轮询该系统回信、修补漏接 webhook 的后台任务 |
| **邮件平台联系人** | 该系统按"列表（list）/ 营销活动（campaign）"组织的潜在客户记录 |

代码与配置里的真实标识符（模块名 `snov_*`、环境变量 `SNOV_*`、表前缀 `snov_`、URL 常量等）**保持原样不动**；本文只在描述其作用时改用上述称呼，并在首次出现时用括号附注真实标识符，方便维护者对照代码。

---

## 1. 技术栈速览

| 层 | 技术 | 关键依赖 |
|---|---|---|
| 后端框架 | FastAPI + Uvicorn | `fastapi>=0.110`、`uvicorn[standard]>=0.27` |
| ORM / 迁移 | SQLAlchemy 2.0 + Alembic | `sqlalchemy>=2.0`、`alembic>=1.13` |
| 定时任务 | APScheduler（进程内） | `apscheduler>=3.10` |
| HTTP 客户端 | httpx | `httpx>=0.27` |
| AI | OpenAI 兼容 SDK（可接 DeepSeek） | `openai>=1.12` |
| Excel | openpyxl | `openpyxl>=3.1` |
| 数据库 | 开发 SQLite / 生产 PostgreSQL | 生产驱动 `psycopg2-binary>=2.9` |
| 前端框架 | Vue 3（Composition API） | `vue ^3.4`、`vue-router ^4.3` |
| UI 库 | Ant Design Vue 4 | `ant-design-vue ^4.1`、`@ant-design/icons-vue ^7.0` |
| 前端构建 | Vite 5 | `vite ^5.1`、`@vitejs/plugin-vue ^5.0` |
| 部署 | Docker Compose | `postgres:16-alpine`、`caddy:2-alpine`、`traefik:v3.7.6` |

> 没有 Celery / Redis / RQ；所有后台任务都在进程内（见 [§10](#10-后台任务与调度总览)）。

---

## 2. 顶层目录结构

```
kol-outreach/
├── backend/                     # FastAPI 后端（本仓库主体）
│   ├── main.py                  # 应用入口 + lifespan + 中间件
│   ├── config.py                # Settings（环境变量加载）
│   ├── db.py                    # engine / SessionLocal / Base / init_db
│   ├── alembic.ini              # Alembic 配置
│   ├── alembic/                 # 迁移脚本（env.py + versions/）
│   ├── api/                     # REST 路由层
│   ├── models/                  # SQLAlchemy ORM
│   ├── services/                # 业务逻辑（AI、邮件平台集成、导出）
│   ├── scripts/                 # 运维 CLI（python -m scripts.xxx）
│   ├── migrations/              # 历史遗留：raw SQL + data_migration.py
│   ├── tests/                   # pytest 风格测试
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
├── frontend/                    # Vue 3 SPA
│   ├── src/
│   │   ├── main.js              # createApp + 注册 Antd + router
│   │   ├── App.vue
│   │   ├── router/index.js
│   │   ├── layouts/MainLayout.vue
│   │   ├── api/index.js         # 单 axios 实例 + 各业务 API 封装
│   │   └── views/               # 7 个页面
│   ├── public/                  # favicon + 候选库导入模板 xlsx
│   ├── vite.config.js           # dev 代理 /api → :8000
│   ├── Dockerfile               # 两段构建 → Caddy 托管 dist/
│   └── package.json
├── docs/                        # 规格与设计文档
├── backups/                     # 备份目录
├── docker-compose.yml           # ⚠ 已废弃（遗留 SQLite 单机版）
├── docker-compose.dev.yml       # 本地开发：仅 PostgreSQL
├── docker-compose.prod.yml      # 生产全栈：PG + backend + Caddy + Traefik
├── .env.prod.example            # 生产环境变量模板
├── README.md
└── DEVELOPMENT.md               # 本文件
```

---

## 3. 后端架构

### 3.1 入口与生命周期 — `backend/main.py`

- `app = FastAPI(lifespan=...)`：`lifespan` 启动时按序调用 `init_db()`（建表/补列）和 `start_snov_scheduler()`（邮件平台回信兜底同步，仅当开关与凭证齐全）；关闭时 `stop_snov_scheduler()`。
- 所有路由统一挂载在 `/api`：`app.include_router(api_router, prefix="/api")`。
- 根路径两个端点：`GET /`（应用信息）、`GET /health`（`{"status":"ok"}`，供 Docker `HEALTHCHECK` 用）。
- **CORS**：`CORSMiddleware` 读取 `settings.cors_origins_list`，允许带凭证。
- **日志防泄密**：`httpx` / `httpcore` / `openai` 三个 logger 强制 `WARNING`，避免把邮件平台 access_token、邮件正文写进日志。
- 可直接运行：`python -m uvicorn main:app --reload --port 8000`（在 `backend/` 下）。

### 3.2 配置层 — `backend/config.py`

- `Settings` 类通过 `python-dotenv` 读取环境变量；**加载顺序**：仓库根 `.env.prod` → `backend/.env` → 进程环境（后者覆盖前者）。
- 含若干**判定属性**而非裸字段：
  - `snov_webhook_is_configured`：拒绝空值、默认占位符、`change-me-*` / `replace-with-*` 这类占位 token。
  - `SNOV_SYNC_INTERVAL_SECONDS`：代码层强制 `min 60`，默认 120。
- ⚠ **已知坑**：`.env.prod.example` 里列了 `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD`，但 `config.py` **并未定义**这两个字段。`api/auth.py` 一旦被调用会直接 `AttributeError`（详见 [§9](#9-安全与权限)）。

### 3.3 数据访问层 — `backend/db.py`

- SQLAlchemy 2.0 风格：`create_engine(settings.DATABASE_URL, ...)`；SQLite 自动加 `check_same_thread=False`。
- `SessionLocal = sessionmaker(autocommit=False, autoflush=False)`；`get_db()` 是 FastAPI 依赖，按请求 yield 一个 `Session`。
- `init_db()` = `Base.metadata.create_all` + 两个遗留补丁函数 `_ensure_snov_contact_schema` / `_ensure_mailbox_schema`（给老 SQLite 库做 `ALTER TABLE`）。

> **硬约束**：`create_all` 与 `_ensure_*` **只用于兼容历史 SQLite**。新表、新列、新索引一律走 Alembic revision（见 [§5.3](#53-alembic-迁移纪律)），禁止在 `_ensure_*` 里加结构变更、禁止手写 `ALTER TABLE` 上生产。设计依据见 `docs/PIPPIT_KOL_DATA_STANDARDIZATION_DESIGN.md`。

### 3.4 API 路由总表 — `backend/api/__init__.py`

| 模块 | 挂载前缀 | Tag | 启用 | 职责 |
|---|---|---|---|---|
| `api/kol.py` | `/api/kols` | KOL | ✅ | KOL 列表/详情、CSV/Excel 导入、开场白生成 |
| `api/operators.py` | `/api/operators` | 运营人员 | ✅ | 运营人员 CRUD + demo 种子 |
| `api/threads.py` | `/api/threads` | 会话 | ✅ | Hot Lead 看板、分派、状态、备注、AI 回填、报价导出 |
| `api/mailbox.py` | `/api/mailbox` | 邮箱 | ✅ | 会话邮箱视图（搜索/过滤/已读/星标/分页） |
| `api/webhook.py` | `/api/webhook` | Webhook | ✅ | 邮件平台 webhook 接收与意向分析调度 |
| `api/stats.py` | `/api/stats` | 统计 | ✅ | 总览与意向分布 |
| `api/snov.py` | `/api/snov` | 邮件平台 | ✅ | 邮件平台连接状态/活动/webhook/联系人同步/历史回信补拉 |
| `api/crawler.py` | `/api/crawler` | 采集 | ✅ | 触发采集后台任务 / 查询进度 / 列可选产品（复刻回填模式） |
| `api/auth.py` | — | — | ❌ | 看板 Basic 鉴权（未注册 + config 缺字段，**死代码**） |
| `api/send.py` | — | — | ❌ | 遗留邮件供应商集成（未注册，**死代码**） |

> 设计取舍：看板当前**公开访问**；唯一的鉴权闸是 webhook 的专用 token（见 [§9](#9-安全与权限)）。`api/__init__.py` 顶部有注释说明这一意图。

### 3.5 各路由端点清单

#### `kol.py` — `/api/kols`
| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `` | 分页列表，过滤 `status` / `niche` / `page` / `size`（size ≤ 500） |
| GET | `/{kol_id}` | 单条详情 |
| POST | `/import-csv` | 爬虫 CSV 批量导入（按 email 大小写不敏感去重） |
| DELETE | `/{kol_id}` | 删除 |
| POST | `/generate-intros` | 批量生成个性化开场白（无 `recent_videos` 的跳过） |
| POST | `/import-candidate` | 上传 28 列"全部候选"xlsx → `kol_candidate` + 晋升 `kol`/`kol_email` |
| POST | `/import-email-collection` | 上传 22/23 列"邮箱采集结果"xlsx（preset：`richup`/`pippit`/`dola`） |

> **路由顺序注记**：`POST /import-csv` 在 `GET /{kol_id}` 之前声明；`/import-candidate`、`/import-email-collection`、`/generate-intros` 这些字面量 POST 在参数 GET/DELETE 之后。当前因 HTTP 方法不同**无冲突**。新增 `POST /{kol_id}/...` 时务必把字面量路由前移，否则会被参数段吞掉。

#### `operators.py` — `/api/operators`
| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `` | 列出启用中的运营人员 |
| POST | `` | 新建（`{name,email,role}`） |
| POST | `/seed` | 创建 Alice/Bob/Carol demo 账号 |

#### `threads.py` — `/api/threads`（Hot Lead 看板）
| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `` | 列表：hot 优先，其次 `intent_score` 倒序；过滤 `status` / `campaign_id` / `assignee_id` / `unassigned_only` |
| POST | `/backfill-profile` | 触发 AI 画像回填后台任务（单并发，冲突返回 409），返回 `job_id` |
| GET | `/backfill-status` | 查询最近一次回填任务进度（内存态） |
| POST | `/export` | 选中会话生成报价 xlsx，`StreamingResponse` + RFC 5987 中文文件名 |
| GET | `/{thread_id}` | 会话详情（含 messages + notes） |
| POST | `/{thread_id}/assign` | 分派给运营人员 |
| POST | `/{thread_id}/status` | 更新状态（open/hot/warming/cooling/closed） |
| POST | `/{thread_id}/notes` | 加内部备注 |

#### `mailbox.py` — `/api/mailbox`
| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `` | 邮箱视图（folder: inbox/sent/starred/bounced；含未读数/最新消息预览的子查询） |
| GET | `/filters` | 可用营销活动 + 收发账号（下拉填充用） |
| PATCH | `/threads` | 批量改已读/星标 |
| PATCH | `/threads/{thread_id}` | 单条改已读/星标 |

#### `webhook.py` — `/api/webhook`
| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/snov` | 邮件平台事件入口。`?token=` 经 `hmac.compare_digest` 校验；解析 sent / reply 事件 → 规范化字段 → 按 `message_id`（缺失时用 sha256 稳定指纹）去重 → upsert KOL/Thread/Message → 后台跑 `analyze_inbound_message`。目标 3 秒内返回 |

#### `snov.py` — `/api/snov`（邮件平台管理 UI）
| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/status` | 连接自检（活动数、webhook 数；**绝不返回密钥**） |
| GET | `/campaigns` | 列出营销活动 |
| GET | `/webhooks` | 列出已配置 webhook |
| POST | `/sync-contacts` | 同步邮件平台联系人到本平台 |
| POST | `/webhooks` | 新建 webhook（校验 `event_object`×`event_action` 合法组合） |
| POST | `/sync-replies` | 历史回信补拉（幂等：按 sha256 指纹去重），**同步**跑意向分析 |

#### `stats.py` — `/api/stats`
| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/overview` | 总数（kols / threads / hot / open） |
| GET | `/intent-distribution` | 意向分布饼图数据 |

### 3.6 services 层 — `backend/services/`

| 模块 | 职责 |
|---|---|
| `ai_intent.py` | **系统大脑**。回信 → 结构化 JSON（intent/score/budget/questions/timeline/summary/action）→ 映射 thread 状态。失败/无 key 退化为 `_rule_based_fallback` 关键词匹配 |
| `ai_personalize.py` | 基于 KOL 近期视频标题生成冷邮件开场白；失败退化为 `_mock_intro` |
| `ai_profile.py` | **AI 回填**：从最近一封入站正文抽 9 字段画像 + `parse_min_quote()` 正则提取最低报价与币种 |
| `snov_client.py` | 邮件平台 API 客户端（httpx） |
| `snov_contacts.py` | 邮件平台联系人同步逻辑 |
| `snov_scheduler.py` | APScheduler 入口（`start/stop_snov_scheduler`），120s 兜底拉回信 |
| `instantly_client.py` | ⚠ 遗留：另一家邮件供应商客户端，未启用 |
| `export_quote.py` | 报价 xlsx 生成（openpyxl） |
| `attachments.py` | 附件处理（邮件平台回信 API 不保证带回附件） |
| `email_content.py` | 邮件正文提取/清洗辅助 |
| `crawler/` | **采集器子包**。关键词发现 → 多平台扩展 → 邮箱抽取 → MX 校验 → 入库。入口 `pipeline.run_crawl()`；业务规则在 `config_rules.py`（产品/关键词/国家白名单/种子）；抓取层 `fetcher.py` 可插拔（首期 httpx） |

### 3.7 scripts 维护命令 — `backend/scripts/`

> 用法：在 `backend/` 下 `python -m scripts.<name>`。每个脚本都设计成**既能 CLI 跑、也能被 HTTP 端点 import 调用**（统一入口 `run_import(...)` / `run_backfill(...)`，接受 `bytes` / `BytesIO` 与 `db: Session`、`commit: bool`）。

| 脚本 | 用途 |
|---|---|
| `import_kol_xlsx.py` | 最老的导入器：清洗 150 人名单 → 写 styled xlsx（导入摘要/清洗后/拒绝/原始）→ upsert `kol` + `project_assessment` + `kol_email`。策略：**最新批次覆盖** Tier-1 字段 |
| `import_kol_candidate.py` | 28 列"全部候选"导入 → `kol_candidate` + 有邮箱行晋升 `kol`/`kol_email`。27 项 `COLUMN_MAP` |
| `import_email_collection.py` | 22/23 列"邮箱采集结果"导入。三 preset（`richup`/`pippit`/`dola`），`detect_preset` 自动识别表头。策略：**增量回填空字段**（不覆盖已有值） |
| `push_to_kol.py` | 薄 CLI 包装：自动判格式 → POST 上传到对应 HTTP 导入端点。默认目标生产域名，支持重试（401/403/422 不重试） |
| `backfill_kol_profile.py` | AI 画像回填 worker，`ThreadPoolExecutor` 默认 4 worker，幂等（仅填空字段，`--force` 覆盖） |
| `reanalyze_messages.py` | 对历史入站消息**重跑意向分析**。**故意不暴露为 HTTP**（每次调用烧钱），需 CLI 触发 |
| `sync_snov_contacts.py` | 一行调用 `services.snov_contacts` 的快捷脚本 |

---

## 4. 数据模型与迁移（核心）

### 4.1 九张表关系

```
                ┌─────────────────┐
                │   operator      │  运营人员（无密码列，Basic 鉴权走环境变量）
                └────────┬────────┘
                         │ assignee_id
                         ▼
┌──────────────┐   ┌──────────────┐         ┌──────────────┐
│ kol_email    │◀──│   thread     │──FK────▶│    kol       │  发信池
│ (多邮箱)     │   │  (会话)      │         │  (有邮箱)    │
│ UNIQUE(kol,  │   │ status/intent│         └──────┬───────┘
│  normalized) │   │ /score/...   │                │ 1—N
└──────────────┘   └──────┬───────┘                ├──▶ project_assessment (多项目评估)
                          │ 1—N                    │      UNIQUE(project_code, kol_id)
                          ├──▶ message (单封邮件)   └──▶ kol_email
                          └──▶ note (内部备注)
                                   │
                                   └─FK─▶ operator

┌──────────────────┐         ┌──────────────┐
│  kol_candidate   │  独立池  │   send_log   │  发送审计（N—1 kol / N—1 thread）
│ (大数据库，无 FK) │         └──────────────┘
└──────────────────┘
```

| 表 | 模型文件 | 关键约束 | 角色 |
|---|---|---|---|
| `kol` | `models/kol.py` | `email` 非空 + 索引；partial unique `ux_kol_email_normalized` on `lower(trim(email))` | **发信池**：有联系邮箱的 KOL |
| `kol_candidate` | `models/kol_candidate.py` | `UNIQUE(platform, account)`；无 FK | **大数据库**：爬虫全量候选（含无邮箱） |
| `kol_email` | `models/kol_email.py` | `UNIQUE(kol_id, email_normalized)`；`FK kol.id CASCADE` | 多邮箱（一 KOL 多邮箱） |
| `project_assessment` | `models/project_assessment.py` | `UNIQUE(project_code, kol_id)`；CHECK `fit_status IN ('fit','not_fit')` | 多项目并行评估（`dola_uk` / `pippit_2026`） |
| `thread` | `models/thread.py` | `FK kol_id`、`FK assignee_id→operator`；`campaign_id` 索引 | 会话聚合（status/last_intent/intent_score/ai_summary） |
| `message` | `models/message.py` | `FK thread_id`；`message_id` 唯一索引；RFC-5256 `in_reply_to`/`references` | 单封邮件（含 `ai_analysis` JSON） |
| `note` | `models/note.py` | `FK thread_id`、`FK operator_id` | 运营内部备注 |
| `operator` | `models/operator.py` | `email` 唯一 | 运营人员 |
| `send_log` | `models/send_log.py` | `FK thread_id`（可空）、`FK kol_id` | 发送审计 |

### 4.2 核心概念对照表

| 术语 | 含义 | 落地 |
|---|---|---|
| **大数据库** | 爬虫产出的全量候选池（含无邮箱账号），作为后续挖掘的素材库 | `kol_candidate` 表；前端 `KolImport.vue` 用 `<a-tag>大数据库</a-tag>` 标识 |
| **发信池** | 只有"有联系邮箱"的候选会晋升到此，是真正能被邮件平台触达的集合 | `kol` 表；导入时 `parse_emails(...)` 非空才晋升 |
| **HotLead** | AI 判定为高意向的回信会话（`intent='high'` 或 `intent_score≥75`），在看板置顶 | `thread.status='hot'`；`HotLeads.vue` |
| **Tier 1 标准化** | 2026-07 重构：拆解 `kol.contact_notes` 文本大字段为结构化列 + 引入 Alembic | `kol` 8 个新列 + `kol_email` + `project_assessment` |
| **多邮箱** | 一个 KOL 可有多个联系邮箱，主邮箱仍镜像到 `kol.email` 以兼容旧逻辑 | `kol_email` 表 |
| **多项目评估** | 同一 KOL 在 Dola / Pippit 等并行项目下可独立评估适配度 | `project_assessment` |

### 4.3 Alembic 迁移纪律

**revision 链**（`backend/alembic/versions/`）：

```
baseline_0000  ──▶  kol_v2_0001  ──▶  kol_candidate_0002
(20260717_0000_baseline)  (0001_kol_v2_schema)  (0002_kol_candidate_pool)
```

- `baseline_0000`：冻结迁移前 schema（6 表，含历史 `_ensure_*` 补丁列与 partial unique index）
- `kol_v2_0001`：纯增量 —— 给 `kol` 加 8 个 Tier-1 列 + 2 索引；建 `project_assessment`（含 CHECK）+ `kol_email`；调 `migrations.data_migration.upgrade_backfill(bind)` 回填
- `kol_candidate_0002`：建 `kol_candidate`（31 列 + UNIQUE + 3 索引）+ SQL 视图 `kol_emailable`

**常用命令**（在 `backend/` 下运行）：

```bash
alembic current                       # 当前版本
alembic upgrade head                  # 升到最新
alembic downgrade -1                  # 回退一版
alembic stamp head                    # 不执行只标记（对齐已有库）
alembic revision -m "xxx" --autogenerate   # 生成新迁移
```

**硬约束**：

1. ✅ 新表 / 新列 / 新索引 → `alembic revision --autogenerate` → 检查生成的脚本 → `upgrade head`
2. ❌ 禁止靠 `Base.metadata.create_all` 上线新结构（它只建不补，且生产已被 Alembic 接管）
3. ❌ 禁止在 `db.py` 的 `_ensure_*` 里加结构变更
4. ❌ 禁止手写 `ALTER TABLE` 上生产
5. ⚠ `env.py` 设了 `compare_type=True`，autogenerate 会捕捉类型变更；新增模型必须在 `models/__init__.py` 里 import 以注册到 `Base.metadata`，否则 autogenerate 看不到

### 4.4 历史遗留迁移产物 — `backend/migrations/`

`20260715_*.sql`、`20260716_*.sql` 与 `data_migration.py` 是 **Alembic 接管之前**的产物。`data_migration.py` 是一个状态机解析器，把老的 `kol.contact_notes` 多行文本（按标签锚定，非按行切，因 `内容证据` 字段内含换行）拆成结构化字段，被 `kol_v2_0001` 调用做一次性回填。

> **勿在此目录新增内容。** 新迁移一律进 `alembic/versions/`。

---

## 5. 关键业务流程

### 5.1 数据入库漏斗（爬虫 → 大数据库 → 发信池）

```
  爬虫产出 xlsx（kol_collect.mjs 等）
            │
            ▼
  push_to_kol.py  或  Web 上传（KolImport.vue）
            │  detect_format()
            ├── 含"全部候选"sheet ─▶ POST /api/kols/import-candidate
            └── 含 {平台,账号,主页链接,联系邮箱} ─▶ POST /api/kols/import-email-collection
            │
            ▼
  scripts.run_import(content, commit=True, db=...)
            │
            ├──▶ 写 kol_candidate（按 platform+account 去重，增量回填空字段）
            │
            └──▶ 当行有邮箱：
                  ├── 写 kol（按 email 去重，主邮箱）
                  └── 写 kol_email（按 kol_id+email_normalized 去重，全部邮箱）
```

- **去重策略差异**：`import_kol_xlsx.py`（老）是"最新批次**覆盖**"；`import_kol_candidate.py` / `import_email_collection.py`（新）是"**只填空字段**，不覆盖已有值"。维护时按需选择。
- **字段截断**：导入器对所有 varchar 字段做 `_trunc()`，按列宽截断，避免 `value too long`。
- **数字解析**：`parse_int` 支持 `K` / `M` 后缀（粉丝数、播放量）。
- **多邮箱拆分**：`parse_emails` 按 `| , ;` 切分，小写去重保序。

### 5.2 邮件会话与意向分析

```
  邮件平台发信/收信事件
            │
            ▼
  POST /api/webhook/snov?token=...
            │
            ├── token 校验（hmac.compare_digest）—— 失败 401，未配置 503
            │
            ├── 解析事件（sent / reply / 旧事件名兼容）
            ├── 规范化字段
            ├── 去重：message_id 优先，缺失则用 sha256 稳定指纹
            │
            ▼
  upsert KOL / Thread / Message
            │
            ▼
  BackgroundTasks: analyze_inbound_message(message.id)
            │
            ▼
  services.ai_intent.analyze_intent
            │
            ▼
  更新 message.ai_analysis（JSON）
       + thread.last_intent / intent_score / ai_summary / status
```

- **兜底同步**：APScheduler 每 `SNOV_SYNC_INTERVAL_SECONDS`（默认 120，min 60）跑 `sync_historical_replies`，主动拉取邮件平台营销活动回信，修补漏接的 webhook。幂等：按 sha256 指纹去重。
- **SLA**：webhook 目标 3 秒内返回（意向分析进后台）。
- **历史回信补拉**端点 `POST /api/snov/sync-replies` 是**同步**跑意向分析的（不入后台），调用方需容忍较长耗时。

### 5.3 HotLead 运营闭环

```
  HotLeads.vue 看板（hot 优先 + intent_score 倒序，每 120s 自动刷新）
            │
            ├──▶ 选中会话 → "AI 补全画像"
            │       │  POST /api/threads/backfill-profile
            │       │  → 后台 ThreadPoolExecutor(4) 跑 backfill_kol_profile
            │       │  → services.ai_profile 抽 9 字段 + parse_min_quote
            │       │  → 合并进 message.ai_analysis（不动已有 intent 字段）
            │       │  前端每 2s 轮询 /backfill-status
            │       ▼
            │     生成结构化画像 + 报价
            │
            └──▶ 选中会话 → "导出选中"
                    │  POST /api/threads/export
                    │  → services.export_quote.build_quote_workbook
                    ▼
                  下载报价 xlsx（中文文件名 RFC 5987）
```

- **单并发约束**：同一时刻只允许一个回填任务（`_backfill_jobs` 字典跟踪），冲突返回 **409**，避免并发 AI 调用翻倍烧钱。
- **幂等**：回填只填空字段（除非 `--force`），任务重启丢失可重跑。

### 5.4 意向 → 状态映射规则

来自 `services/ai_intent.intent_to_thread_status`：

| AI intent | intent_score | thread.status |
|---|---|---|
| `high` | 或 ≥ 75 | **hot** |
| `medium` | 40–74 | warming |
| `low` | < 40 | open |
| `negative` | — | closed |
| `ooo` / `auto_reply` | — | 按规则降级处理 |

---

## 6. AI 集成维护点

| 服务 | 模型（默认） | 温度 | 输出 | 降级 |
|---|---|---|---|---|
| `ai_intent`（意向） | `OPENAI_MODEL_INTENT=gpt-4o-mini` | 0.1 | 严格 JSON（`response_format=json_object`） | `_rule_based_fallback` 关键词 |
| `ai_personalize`（开场白） | `OPENAI_MODEL_PERSONALIZE=gpt-4o` | 0.8 | 自然文本 | `_mock_intro` |
| `ai_profile`（画像/报价） | 同上 | — | 9 字段 JSON + `parse_min_quote` 正则 | — |

**OpenAI 兼容**：
- `OPENAI_BASE_URL` 可指向任意兼容端点（生产可接 DeepSeek）。
- DeepSeek 需要 `extra_body={"thinking": {"type": "disabled"}}`，已在 `ai_intent` 内置。

**安全约束**：
- `openai` logger 强制 `WARNING`，**邮件正文绝不入日志**。
- 无 `OPENAI_API_KEY` 时全部退化为本地规则/ mock，系统仍可跑（但意向质量下降）。

---

## 7. 前端架构

入口链路：`src/main.js` → `createApp(App)` 注册 Antd + router → `App.vue`（仅 `<router-view/>`）→ `router/index.js` → `layouts/MainLayout.vue`。

### 路由与视图

| 路由 | 视图 | 菜单 | 职责 |
|---|---|---|---|
| `/dashboard` | `Dashboard.vue` | 总览 | 概览 |
| `/hot-leads` | `HotLeads.vue` | Hot Lead | 运营主工作台：看板/分派/AI 回填/导出；每 120s 刷新；回填任务每 2s 轮询 |
| `/mailbox` | `Mailbox.vue` | 邮箱 | 会话列表：搜索/过滤/已读/星标/分页 |
| `/threads/:id` | `ThreadDetail.vue` | （无） | 会话详情 + 回复（侧栏仍高亮"邮箱"） |
| `/kols` | `KolList.vue` | KOL 列表 | KOL 列表 + 批量操作 |
| `/kol-import` | `KolImport.vue` | 导入 KOL | 两个卡片：候选池 xlsx / 爬虫 CSV；含 28 列规格表 + 模板下载 |
| `/stats` | `Stats.vue` | 统计 | 统计图表 |

### API 封装 — `src/api/index.js`

- 单个 axios 实例：`baseURL: '/api'`、`timeout: 30000`。
- 响应拦截器剥 `resp.data`，业务代码直接拿 payload。
- 导出分组：`kolApi` / `threadApi`（含 `backfillProfile` / `backfillStatus` / `exportQuotes`）/ `mailboxApi` / `operatorApi` / `statsApi` / `snovApi`。

### 维护点

- **dev 代理**：`vite.config.js` 把 `/api` 代理到 `http://localhost:8000`，开发期免 CORS。
- **构建产物**：`npm run build` → `frontend/dist/`，生产由 Caddy 托管并反代 `/api`。
- **环境变量**：前端只有 `VITE_SNOV_INBOX_URL`（"New Mail" 按钮跳转目标，构建期注入）。

---

## 8. 安全与权限

| 机制 | 状态 | 说明 |
|---|---|---|
| 邮件平台 webhook token | ✅ 在用 | `?token=` 经 `hmac.compare_digest` 比对 `SNOV_WEBHOOK_TOKEN`；未配置返回 503，不匹配返回 401 |
| 采集端点 token | ✅ 在用 | 采集三端点强制 `X-Crawler-Token` 头（`Depends(require_crawler_token)`），`hmac.compare_digest` 比对 `CRAWLER_TOKEN`；未配置/占位符返回 503，不匹配 401。触发采集是带副作用写入（真实抓取 + DNS + 写库），不能像只读看板那样公开 |
| 看板 Basic 鉴权 | ❌ 死代码 | `api/auth.py` 定义了 `require_dashboard_auth`，但**未注册到任何路由**，且 `config.py` **未定义** `DASHBOARD_USERNAME/PASSWORD`，调用即 `AttributeError`。`.env.prod.example` 里却列了这俩变量 —— 遗留陷阱 |
| 日志防泄密 | ✅ 在用 | `httpx` / `httpcore` / `openai` 强制 WARNING，防 access_token / 邮件正文落盘 |
| 发送安全阀 | ⚠ 仅配置 | `MAX_DAILY_SEND_PER_MAILBOX=30`、`WARMUP_MIN_DAYS=14`。**本平台不发信**，仅作为约束配置留存 |

**设计取舍**：
- 看板（只读）**有意公开访问**（`api/__init__.py` 顶部注释明示），依赖网络层隔离。
- 写入类端点必须有鉴权闸：webhook 用 query token，采集端点用请求头 token。两者未配置都返回 503（宁可关闭也不公开带副作用的端点）。
- **采集 token 的局限**：前端用 `VITE_CRAWLER_TOKEN` 构建期注入，会出现在前端 bundle 里。所以它只挡公网随机扫描/跨域调用，真正的隔离靠部署层（私有域名 + 内网）。前后端必须同步设置同一个值。
- **采集代理池**：数据中心 IP 几乎必被 YouTube 封，生产环境必须配 `CRAWLER_PROXIES`（住宅代理，逗号分隔）。Fetcher 按 round-robin 轮换，遇 403/429 自动换下一个代理重试。未配置则直连（仅适合本地开发）。
- 如需给看板上鉴权，需先在 `config.py` 补 `DASHBOARD_*` 字段并修复 `auth.py`，再注册依赖。

---

## 9. 后台任务与调度总览

| 机制 | 用途 | 实现 | 跨重启？ |
|---|---|---|---|
| APScheduler | 邮件平台回信兜底同步（默认 120s） | `services/snov_scheduler.py`，`BackgroundScheduler(timezone="UTC")`，`coalesce=True`、`max_instances=1` | ❌ 进程内 |
| FastAPI BackgroundTasks | 单条回信触发意向分析 | `api/webhook.py` 调度 `analyze_inbound_message` | ❌ |
| FastAPI BackgroundTasks | AI 画像回填（4 worker 线程池） | `api/threads.py` + `scripts.backfill_kol_profile`，状态存 `_backfill_jobs` | ❌ 内存态 |

**重要约束**：
- **无 Celery / Redis / RQ**，所有任务在进程内。进程重启 = 进行中的任务丢失。
- 上述任务都设计成**幂等可重跑**：回信去重靠 `message_id` / sha256；回填只填空字段；同步任务靠 `_historical_message_id` 指纹。所以重启后由 APScheduler 或人工触发即可补回。
- 长耗时任务（如 `sync-replies`）是**同步**执行的，没入后台，注意超时风险。

---

## 10. 环境与运行

### 配置加载顺序

`config.py` 读取顺序（后者覆盖前者）：
1. 仓库根 `.env.prod`（生产）
2. `backend/.env`（本地开发）
3. 进程环境变量

### 本地开发

```bash
# 1) 起 PostgreSQL（仅数据库，后端仍在本机 uvicorn 跑）
docker compose -f docker-compose.dev.yml up -d    # PG 在 localhost:5432

# 2) 后端
cd backend
pip install -r requirements.txt
cp .env.example .env            # 改 DATABASE_URL 指向本地 PG，或保留 sqlite
python -m uvicorn main:app --reload --port 8000

# 3) 前端
cd frontend
npm install
npm run dev                    # http://localhost:5173，/api 自动代理到 :8000
```

### 生产部署

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
```

栈：`postgres:16-alpine` + backend（uvicorn）+ frontend（Caddy 托管 `dist/` + 反代 `/api`）+ `traefik:v3.7.6`（TLS，HTTP-01 证书）。

### 关键环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./kol_outreach.db` | 生产用 `postgresql+psycopg2://...` |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:3000` | 逗号分隔 |
| `OPENAI_API_KEY` | — | 缺失则 AI 全部降级 |
| `OPENAI_BASE_URL` | — | 兼容端点（DeepSeek 等） |
| `OPENAI_MODEL_INTENT` | `gpt-4o-mini` | 意向分析模型 |
| `OPENAI_MODEL_PERSONALIZE` | `gpt-4o` | 开场白/画像模型 |
| `SNOV_WEBHOOK_TOKEN` | — | 邮件平台 webhook 校验 token（占位符会被拒绝） |
| `SNOV_CLIENT_ID` / `SNOV_CLIENT_SECRET` | — | 邮件平台 API 凭证（启用定时同步必需） |
| `SNOV_SYNC_ENABLED` | — | 定时同步开关 |
| `SNOV_SYNC_INTERVAL_SECONDS` | 120 | 同步间隔（min 60） |
| `MAX_DAILY_SEND_PER_MAILBOX` | 30 | 发送安全阀（仅配置） |
| `WARMUP_MIN_DAYS` | 14 | 预热安全阀（仅配置） |
| `CRAWLER_TOKEN` | — | 采集端点鉴权（必填，否则 503；前端用 `VITE_CRAWLER_TOKEN` 同值） |
| `CRAWLER_PROXIES` | — | 住宅代理池（逗号分隔，生产必填；留空直连仅适合本地） |
| `CRAWLER_MAX_CONCURRENCY_*` | 3/6/30 | 发现 / 抓取 / MX 三阶段并发上限 |
| `CRAWLER_REQUEST_TIMEOUT` / `INTERVAL` | 20 / 0.2 | 单页超时（秒）/ 请求间隔（秒） |
| `VITE_SNOV_INBOX_URL` | — | 前端"New Mail"跳转目标，构建期注入 |
| `VITE_CRAWLER_TOKEN` | — | 前端采集 token，须与后端 `CRAWLER_TOKEN` 一致，构建期注入 |

---

## 11. 测试

`backend/tests/`（pytest 风格）：

| 文件 | 覆盖 |
|---|---|
| `test_mailbox.py` | 邮箱视图查询、过滤、分页、批量更新 |
| `test_snov_contacts.py` | 邮件平台联系人同步逻辑 |
| `test_snov_sync.py` | 邮件平台回信同步与去重 |

运行：在 `backend/` 下 `pytest`（或 `python -m pytest`）。目前**无前端测试、无 E2E 测试**。

---

## 12. 常见维护任务（Cookbook）

### 12.1 新增一张表 / 一列

```bash
cd backend
# 1) 在 models/ 新建模型文件，并在 models/__init__.py 里 import 注册
# 2) 生成迁移
alembic revision -m "add xxx table" --autogenerate
# 3) 检查 backend/alembic/versions/ 下新生成的脚本（删掉不必要的 drop、补 index）
# 4) 本地验证
alembic upgrade head
# 5) 生产部署时同样的 upgrade head 自动跑（或手动）
```

⚠ 切勿偷懒用 `create_all` —— 它在生产已存在的库上不会补结构。

### 12.2 新增一个 API 端点

1. 在对应 `api/<module>.py` 加路由函数。
2. **路由顺序**：字面量路径（如 `/export`）必须声明在参数路径（`/{id}`）之前，否则被参数段吞掉。
3. 若是新模块：在 `api/__init__.py` 注册 `api_router.include_router(...)`。
4. 复杂逻辑下沉到 `services/`，路由层只做参数校验 + 编排。

### 12.3 新增一个前端页面

1. `src/views/Xxx.vue` 写视图。
2. `src/router/index.js` 加路由（作为 `MainLayout` 子路由）。
3. `src/layouts/MainLayout.vue` 加菜单项（icon 用 `@ant-design/icons-vue`）。
4. 需要调接口就在 `src/api/index.js` 加方法。

### 12.4 新增一个导入格式

1. 在 `backend/scripts/import_email_collection.py` 的 `PRESETS` 加一项（sheet 名、列映射、备注列）。
2. 若表头特征明显，更新 `detect_preset` 让无 `--preset` 时也能自动识别。
3. 复用 `run_import(...)` 的"双写 `kol_candidate` + 晋升 `kol`/`kol_email`"模式，**不要新写一套去重逻辑**。
4. CLI 与 HTTP 端点共用同一 `run_import`，无需改路由。

### 12.5 重跑历史意向分析

```bash
cd backend
python -m scripts.reanalyze_messages --workers 4
```

> **故意不暴露为 HTTP 端点**：每次调用都烧 AI token。只能 CLI 触发。

### 12.6 回滚一次迁移

```bash
cd backend
alembic downgrade -1          # 回退最近一版
alembic current               # 确认
```

⚠ 含数据回填的迁移（如 `kol_v2_0001`）回滚不会还原已回填的数据，只还原结构。

### 12.7 新增一个采集产品 / 关键词

1. 编辑 `backend/services/crawler/config_rules.py`：
   - 在 `productTerms` 加产品名 → 关键词列表
   - 在 `allowedByProduct` 加该产品的允许国家集合（空国家视为全合格）
2. 重启后端，`GET /api/crawler/products` 即可见新产品，前端复选框自动出现。
3. 无需改表、无需迁移（`kol_candidate.fit_product` / `recommend_product` 是 varchar）。

> 采集的抓取正则（YouTube 页面结构）集中在 `services/crawler/youtube.py`；改版时只改这一处。

### 12.8 采集抗反爬升级（换 Fetcher）

采集器抓取层是可插拔的 `Fetcher` 接口（`services/crawler/fetcher.py`）：
- 现状：`HttpxFetcher`（裸 httpx + 正则解析 HTML）
- 升级代理 / 无头浏览器渲染：新增一个 Fetcher 实现类，实现 `fetch_text` / `resolve_mx` / `aclose`，在 `pipeline._run_async` 里换成新实现即可。业务规则与解析逻辑不动。

---

## 13. 已知遗留 / 技术债（维护者必读）

| 项 | 位置 | 处理建议 |
|---|---|---|
| `create_all` + `_ensure_*` 与 Alembic 并存 | `backend/db.py` | 新结构走 Alembic；`_ensure_*` 勿扩展 |
| 遗留邮件供应商集成 | `backend/api/send.py`、`services/instantly_client.py` | 未注册/未启用，可清理 |
| 看板鉴权死代码 | `backend/api/auth.py` | 未注册且 `config.py` 缺 `DASHBOARD_*`，调用即崩；要么补齐要么删 |
| 根目录冗余文档副本 | `HANDOFF.md`、`NAS_DEPLOY.md`、`PREREQUISITES.md`、`INSTANTLY_*.md` | `docs/` 下已有新版，根目录副本可考虑清理 |
| 根目录遗留脚本 | `backend/sync_kol_to_db.py` | 独立同步助手，功能已被 scripts/ 覆盖 |
| `kol.py` 路由顺序 | `backend/api/kol.py` | 字面量 POST 在参数 GET/DELETE 之后，当前无冲突；新增 `POST /{id}/...` 需前移 |
| 内存态任务状态 | `_backfill_jobs`、`_historical_message_id` | 重启即丢，靠幂等重跑兜底 |
| 废弃 compose | `docker-compose.yml`（根） | 已被 `dev` / `prod` 取代，可清理 |
| `security_lab/` | 已从工作树删除（仅 `git show 245715c:` 可查） | 历史实验，无需维护 |

---

## 14. 文档索引

| 文档 | 定位 |
|---|---|
| [`README.md`](README.md) | 产品介绍、快速启动、CSV 格式、API 速览 |
| [`docs/PROJECT_SPEC.md`](docs/PROJECT_SPEC.md) | 正式需求规格（RFC 风格） |
| [`docs/DATA_CONSTRAINTS.md`](docs/DATA_CONSTRAINTS.md) | 三大数据来源字段级约束 |
| [`docs/PIPPIT_KOL_DATA_STANDARDIZATION_DESIGN.md`](docs/PIPPIT_KOL_DATA_STANDARDIZATION_DESIGN.md) | Tier 1 数据标准化设计（Alembic 引入依据） |
| [`docs/NAS_DEPLOY.md`](docs/NAS_DEPLOY.md) | NAS 部署 + 邮件平台 webhook 配置 |
| [`docs/PREREQUISITES.md`](docs/PREREQUISITES.md) | 第三方账号准备清单 |
| **`DEVELOPMENT.md`**（本文） | 架构维护手册 |
