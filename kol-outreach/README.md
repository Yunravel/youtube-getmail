# KOL 外联中台

> Snov 发信/收信 → 中台同步邮件会话 → AI 分析回信意向 → 运营人员人工跟进

## ✨ 核心功能

- **Snov 邮件同步**:接收 webhook，并每 2 分钟调用 Snov API 补拉回信；保存邮件正文、联系人邮箱、Campaign 任务名和可用的附件元数据
- **AI 意向分析**:KOL 回信自动分级(Hot/Medium/Low/Negative),运营只看高意向
- **运营 Web 中台**:Hot Lead 看板 / 邮箱与会话详情 / 分配 / 内部备注
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

访问 http://localhost:8000/docs 看 API 文档

### 2. 前端

```bash
cd frontend
npm install
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

爬虫产出 CSV,列定义:

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

## 🗄️ 数据库表

| 表 | 说明 |
|----|------|
| kol | KOL 主表(爬虫产出) |
| thread | 邮件会话(一个KOL一个,聚合往来邮件) |
| message | 邮件消息(每封收/发记录,含 AI 分析 JSON) |
| operator | 运营人员 |
| note | 内部备注(运营间私聊) |
| send_log | 历史发送日志表（当前由 Snov 同步 message 会话） |

## 🔌 API 速览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/kols/import-csv | 批量导入 KOL |
| GET | /api/kols | KOL 列表(分页+筛选) |
| GET | /api/threads | 会话看板(按意向分排序) |
| GET | /api/threads/{id} | 会话详情(邮件+AI+备注) |
| POST | /api/threads/{id}/assign | 分配运营 |
| POST | /api/webhook/snov | 接 Snov 已发/回信(回信自动 AI 分析) |
| GET | /api/stats/overview | 总览统计 |

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

### Snov 两分钟同步与附件限制

- 后端在配置 `SNOV_CLIENT_ID`、`SNOV_CLIENT_SECRET` 后，每 120 秒补拉一次 Campaign 邮件回复。
- 前端 Hot Lead 和会话详情页也每 120 秒自动刷新。
- 同步内容包含邮件主题、正文、KOL 邮箱和 Campaign 名称（任务名）。
- Snov 当前公开的 Campaign 回复 API 不保证返回附件或附件下载地址。若 webhook 或未来 API 响应包含附件字段，中台会保存并展示文件名、类型、大小和安全下载地址；API 未返回时附件列表为空。
- 如果业务必须获取所有附件文件，需要另行接入发信邮箱的 IMAP、Gmail API 或 Microsoft Graph，不能仅依赖 Snov Campaign 回复 API。

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
