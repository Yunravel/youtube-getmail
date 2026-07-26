# KOL 外联中台

> Snov 发信/收信 → 中台同步邮件会话 → AI 分析回信意向 → 运营人员人工跟进

## ✨ 核心功能

- **Snov 邮件同步**:接收普通回信与自动回复 webhook，并每 2 分钟调用 Snov API 补拉普通回信；保存邮件正文、联系人邮箱、Campaign 任务名和可用的附件元数据
- **AI 意向分析**:KOL 回信自动分级(Hot/Medium/Low/Negative),运营只看高意向
- **运营 Web 中台**:Hot Lead 看板 / 邮箱与会话详情 / 分配 / 内部备注
- **邮箱收件管理**:会话搜索、Campaign/收发账户筛选、已读、星标、附件提示和分页；每 2 分钟刷新
- **KOL 采集(内嵌爬虫)**:按产品关键词发现 YouTube 频道 → 抓取 about 公开邮箱(MX 校验) → 多平台扩展(IG/TikTok/X) → 入大数据库与发信池;Web UI 触发后台任务,实时进度
- **人工跟进**:中台不代发邮件；运营在 Snov 中人工回复

## 🏗️ 架构

```
Snov Campaign 发信 ──→ campaign_email/sent webhook ──┐
Snov 收到回信 ──────→ campaign_reply/received webhook ─┼→ [中台存会话] → [AI 分级] → [Hot Lead 看板]
                                                        │                              ↓
                                                        └──────────────────→ 人工回到 Snov 跟进
```

## 🛠️ 技术栈

| 层 | 技术 |
|----|------|
| 后端 | FastAPI + SQLAlchemy + APScheduler |
| 前端 | Vue3 + Vite + Ant Design Vue |
| 数据库 | SQLite(开发) / PostgreSQL(生产) |
| AI | OpenAI GPT-4o-mini(意向) / GPT-4o(开场白) |
| 采集 | httpx + dnspython(MX 校验) + 可插拔 Fetcher |
| 邮件来源 | Snov.io Webhook |
| 部署 | Docker Compose |

## 📁 目录结构

```
kol-outreach/
├── backend/
│   ├── main.py                 # FastAPI 入口
│   ├── config.py               # 配置(环境变量)
│   ├── db.py                   # 数据库连接
│   ├── models/                 # 6 张表
│   │   ├── kol.py              # KOL 主表
│   │   ├── thread.py           # 邮件会话
│   │   ├── message.py          # 邮件消息
│   │   ├── operator.py         # 运营人员
│   │   ├── note.py             # 内部备注
│   │   └── send_log.py         # 发送日志
│   ├── api/                    # REST 接口
│   │   ├── kol.py              # KOL CRUD + CSV导入 + 生成开场白
│   │   ├── threads.py          # 会话看板/分配/详情
│   │   ├── webhook.py          # 接 Snov 已发/回信 + AI分析
│   │   ├── operators.py        # 运营人员管理
│   │   └── stats.py            # 统计
│   └── services/               # 业务逻辑
│       ├── ai_intent.py        # ⭐ AI 意向分析(核心)
│       ├── ai_personalize.py   # GPT 开场白生成
├── frontend/
│   └── src/
│       ├── views/              # 6 个页面
│       │   ├── Dashboard.vue   # 总览
│       │   ├── HotLeads.vue    # Hot Lead 看板
│       │   ├── ThreadDetail.vue# 会话详情 + 回复
│       │   ├── KolList.vue     # KOL 列表 + 批量操作
│       │   ├── KolImport.vue   # CSV 导入
│       │   └── Stats.vue       # 统计
│       └── api/                # axios 封装
├── docs/
│   ├── NAS_DEPLOY.md           # NAS 部署 + Snov webhook 配置
│   └── PREREQUISITES.md        # 第三方账号准备清单
└── docker-compose.yml          # 一键部署
```

## 🚀 快速开始(本地开发)

> 看板当前为免登录访问；请仅在受信任的网络或域名访问。Snov webhook 仍使用独立 token 校验。

### 1. 后端

```bash
cd backend
pip install -r requirements.txt

# 配置环境变量
copy .env.example .env
# 编辑 .env(至少填 OPENAI_API_KEY,没填也能跑,走规则兜底)
# 主动同步 Snov 还需要 SNOV_CLIENT_ID / SNOV_CLIENT_SECRET
# 默认 SNOV_SYNC_INTERVAL_SECONDS=120（两分钟）

# 启动(自动建表)
python -m uvicorn main:app --reload --port 8000
```

### 清洗导入与公开资料补全

清洗 Excel 并预检（不写数据库）：

```powershell
python -m scripts.import_kol_xlsx "源文件.xlsx" --output "清洗结果.xlsx"
```

确认摘要后增加 `--commit` 执行导入。导入按小写邮箱去重：新邮箱创建 KOL，已有邮箱只补充空字段，不覆盖人工维护的状态和资料。


访问 http://localhost:8000/docs 看 API 文档

### 2. 前端

```bash
cd frontend
npm install
# 可选：复制 .env.example 为 .env，配置点击“新邮件”后跳转的 Snov 地址
npm run dev
```

访问 http://localhost:5173

### 3. 一键创建演示数据(可选)

```bash
# 创建演示运营人员(Alice/Bob/Carol)
curl -X POST http://localhost:8000/api/operators/seed

# 导入测试 KOL(自己准备 CSV,或用模板)
curl -X POST http://localhost:8000/api/kols/import-csv -F "file=@你的.csv"

# 模拟一封 Snov 回信(测试 AI 分析)
curl -X POST "http://localhost:8000/api/webhook/snov?token=你的SNOV_WEBHOOK_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"event_object":"campaign_reply","event_action":"received","email":"KOL邮箱","subject":"re: partnership","body":"Interested! Send pricing.","message_id":"test-001"}'
```

然后打开 http://localhost:5173/hot-leads 看 Hot Lead 看板。

## 📊 CSV 导入格式(爬虫对接)

导入接口兼容原爬虫字段与 Snov 联系人模板字段。两种格式都以 `email`
作为唯一联系人键，重复执行不会新增重复联系人。

原爬虫 CSV 列定义:

| 列名 | 必填 | 说明 |
|------|------|------|
| name | ✅ | 博主名 |
| email | ✅ | 联系邮箱 |
| channel_id | | YouTube channel ID |
| channel_url | | 频道链接 |
| subscribers | | 粉丝数(数字) |
| country | | 国家 |
| niche | | 赛道 |
| recent_videos | | 最近视频标题,多个用 `\|` 分隔 |

示例:
```csv
name,email,channel_url,subscribers,country,niche,recent_videos
MrBeast,contact@mrbeast.com,https://youtube.com/@MrBeast,320000000,US,entertainment,I Built a City|50 Hours In Solitary
```

Snov 模板字段包括 `fullName`、`firstName`、`lastName`、`locality`、
`position`、`companyName`、`companySite`、`phones`、
`socialLinks[linkedIn]`、`customFields[...]` 与 `listId`。其中 `email`
必填，一行只填写一个有效邮箱。

从已配置的 Snov 账号同步全部现有联系人：

```bash
cd backend
python -m scripts.sync_snov_contacts
```

## 🗄️ 数据库表

| 表 | 说明 |
|----|------|
| kol | KOL/联系人主表（兼容爬虫字段与 Snov 联系人模板字段） |
| thread | 邮件会话(一个KOL一个,聚合往来邮件) |
| message | 邮件消息(每封收/发记录,含 AI 分析 JSON) |
| operator | 运营人员 |
| note | 内部备注(运营间私聊) |
| send_log | 历史发送日志表（当前由 Snov 同步 message 会话） |
| feishu_sync_task | 飞书一达人一行的持久同步、错误重试和对账状态 |

## 🔌 API 速览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/kols/import-csv | 批量导入 KOL |
| GET | /api/kols | KOL 列表(分页+筛选) |
| POST | /api/snov/sync-contacts | 从 Snov 全部有效名单同步联系人 |
| POST | /api/snov/prospect-lists/from-kols | 将勾选的待发 KOL 写入新建的 Snov 待发送名单 |
| GET | /api/threads | 会话看板(按意向分排序) |
| GET | /api/threads/{id} | 会话详情(邮件+AI+备注) |
| GET | /api/mailbox | 邮箱会话列表、搜索、筛选、分页与文件夹计数 |
| GET | /api/mailbox/filters | 邮箱 Campaign 和收发账户筛选项 |
| PATCH | /api/mailbox/threads/{id} | 更新单个会话已读/星标状态 |
| PATCH | /api/mailbox/threads | 批量更新会话已读/星标状态 |
| POST | /api/threads/{id}/assign | 分配运营 |
| POST | /api/webhook/snov | 接 Snov 已发/回信(回信自动 AI 分析) |
| GET | /api/stats/overview | 总览统计 |
| GET | /api/feishu/status | 飞书持久任务状态 |
| GET | /api/feishu/audit | 只读对账：缺行、重复和字段差异统计 |
| POST | /api/feishu/reconcile | 将所有有回信的达人加入全量对账队列 |
| POST | /api/feishu/process | 立即处理当前到期的飞书任务 |
| POST | /api/feishu/retry-conflicts | 重复行人工处理后重新释放冲突任务 |

完整文档见 http://localhost:8000/docs

## 🤖 AI 意向分析逻辑

回信进来后,GPT-4o-mini 输出结构化 JSON:

```json
{
  "intent": "high",           // high/medium/low/negative/ooo/auto
  "intent_score": 85,          // 0-100
  "budget_mentioned": null,    // 提到的报价
  "key_questions": ["报价多少?"],
  "timeline": "flexible",
  "summary": "明确表达合作兴趣并询问预算",
  "suggested_action": "立即跟进"
}
```

阈值映射:
- score ≥ 75 或 intent=high → **hot**(推到看板顶部)
- 40-74 → warming
- < 40 → open
- negative → closed(自动关单)

> 没配 OPENAI_API_KEY 时走关键词规则兜底,配了自动切 GPT。

DeepSeek Flash 使用 OpenAI 兼容接口：

```env
OPENAI_API_KEY=你的DeepSeekKey
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL_INTENT=deepseek-v4-flash
OPENAI_MODEL_PERSONALIZE=deepseek-v4-flash
```

配置模型后，可以在 `backend` 目录重新分析历史规则结果：

```bash
python -m scripts.reanalyze_messages --workers 4
```

### Snov 两分钟同步与附件限制

- 后端在配置 `SNOV_CLIENT_ID`、`SNOV_CLIENT_SECRET` 后，每 120 秒补拉一次 Campaign 邮件回复。
- 前端 Hot Lead 和会话详情页也每 120 秒自动刷新。
- 同步内容包含邮件主题、正文、KOL 邮箱和 Campaign 名称（任务名）。
- Snov 当前公开的 Campaign 回复 API 不保证返回附件或附件下载地址。若 webhook 或未来 API 响应包含附件字段，中台会保存并展示文件名、类型、大小和安全下载地址；API 未返回时附件列表为空。
- 如果业务必须获取所有附件文件，需要另行接入发信邮箱的 IMAP、Gmail API 或 Microsoft Graph，不能仅依赖 Snov Campaign 回复 API。

### 飞书可靠同步

飞书采用“一位达人一行”，数据库是权威数据源。同步按表头名称映射，不依赖
固定 A–R 位置；`系统KOL ID` 是稳定行键，邮箱仅用于兼容历史行。目标表缺少
这两个系统列时会自动追加。

- 代理商、最近发稿、阅读、互动、认证和 CPM 是运营字段，更新达人时不会覆盖。
- 达人画像按 KOL 主表的多个候选字段补齐；合作方式与报价从该达人全部回信中
  取最新非空结果，后续无报价的回信不会清空历史报价。
- 飞书四个业务列采用固定语义：`达人标签` 为每人 2–3 个内容标签；
  `回信所属项目` 来自最新入站会话的 Snov Campaign；`完整报价` 汇总所有
  平台、内容形式、套餐与数量阶梯；`最低报价` 从结构化金额中按币种分别取
  最低值，不会把 USD、GBP、EUR 相互比较；`意向` 直接同步数据库最新回信
  intent；时间字段只保留一个 `回信时间`。
- `auto_reply` 和 `ooo` 属于无效自动回信，不进入最终飞书名单，也不参与
  报价、意向或回信时间聚合；若同一达人后续收到真人回信，则以最新真人回信
  重新进入同步。
- 无可靠来源的数据写“待补全”“待分类”“待采集”“未确认”或“未提供”，
  不留空、不编造。
- 报价可来自邮件正文、本地 PDF/图片附件或受信任的公开报价页。PDF 优先读取
  文本，扫描件和图片走 OCR，Canva、Passionfroot 等动态页面使用无登录浏览器
  渲染；提取文本再交给报价专用 AI 提示词结构化，网页案例收入、订阅数据和
  购物车 `$0` 不计为达人报价。
- 同步失败写入 `feishu_sync_task`，按指数退避重试；服务重启不会丢任务。
- 每 30 分钟把全部有回信的达人重新加入对账，配置项分别为
  `FEISHU_SYNC_INTERVAL_SECONDS` 和 `FEISHU_RECONCILE_INTERVAL_SECONDS`。
- 若飞书存在重复邮箱或重复系统 ID，同步会拒绝任选一行覆盖，并在任务错误中
  标明冲突行号，任务进入 `conflict` 且不会自动重试；运营确认并清理重复行后
  调用 `/api/feishu/retry-conflicts` 重新释放。

## 报价自动回复

该功能只对包含明确金额和币种的真实回信生效。正文、PDF、扫描 PDF 和常见图片报价单会被解析；无法可靠解析的任务会进入人工处理，不会自动发送。

上线步骤：

1. 在 `backend` 目录运行 `alembic upgrade head`。
2. 重新构建后端镜像，以安装 Tesseract、PDF 和图片解析依赖。
3. 打开“邮箱配置”，填写 SMTP 参数并逐个点击“测试 SMTP”（测试只登录和 NOOP，不发送邮件）。
4. 保存并启用全局默认模板；需要时为具体 Snov Campaign 建覆盖模板。
5. 最后开启“自动发送总开关”。该开关默认关闭。

草稿在邮件到达后的 60–120 分钟窗口内随机发送。排队期间可在会话详情中预览、编辑或取消；新回信或外部人工出站邮件会自动取消旧任务。

## 📋 开发进度

- [x] Day1-2: 项目骨架(FastAPI + Vue3 + Docker)
- [x] Day3-4: 数据库建模(6表) + KOL CSV导入
- [x] Day5: GPT 个性化开场白生成
- [x] Day6-7: AI 意向分析核心模块 + webhook 接入
- [x] Snov 收发邮件 webhook 同步 + AI 意向分析
- [x] Hot Lead 看板和会话详情
- [ ] 生产部署与 Snov webhook 订阅验证

## 📖 更多文档

- [NAS 部署与 Snov webhook](./docs/NAS_DEPLOY.md)

## ⚠️ 合规提醒

- 邮件必须提供 unsubscribe(CAN-SPAM)
- 欧盟 KOL 注意 GDPR
- 内容必须是真实业务合作意向,非纯营销
- 严禁爬取非公开联系方式

## License

Private
