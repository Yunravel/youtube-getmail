# KOL 外联中台 — 工程交接文档

> 本文档供接手开发的 AI/工程师阅读,完整说明项目背景、当前状态、架构、待办事项。

---

## 一、项目背景(必读)

### 1.1 这是什么

这是一个**给 KOL 营销代理商(Richup Media)用的"外联中台"**。代理商用 Snov.io 完成发信和人工跟进；中台负责同步已发/回信、分析意向和展示会话。

中台的作用 = **把发出去的邮件,变成 AI 筛过的有效意向 + 客户看板 + 计费报表**。

### 1.2 业务流程

```
Snov.io 发信/收信 → webhook
                                    ↓
                              中台同步收发邮件
                                    ↓
                          GPT AI 意向分析(分级打分)
                                    ↓
                          Hot Lead 看板(运营接手)
                                    ↓
                          转交客户 + 计费报表
```

### 1.3 当前客户

```
客户1: Dola (字节跳动 AI 助手)
   → 38 个 KOL(已在 Snov)
   → 已写好邮件文案(见下方"邮件文案"章节)

客户2: Mango (多产品矩阵: Dreamina/Pippit/Kimi/Dola)
   → 194 个 KOL(已在 Snov,按产品分4个列表)
   → 已写好邮件文案(含 A/B 测试)
```

---

## 二、技术栈

```
后端:     FastAPI + SQLAlchemy + APScheduler (Python 3.12)
数据库:   SQLite(开发) / PostgreSQL(生产)
前端:     Vue3 + Vite + Ant Design Vue
AI:       OpenAI GPT-4o-mini(意向分析) / GPT-4o(开场白)
邮件来源: Snov.io Webhook
部署:     Docker Compose (目标:飞牛 NAS)
```

---

## 三、项目结构

```
kol-outreach/
├── HANDOFF.md                    ← 本文档(交接说明)
├── README.md                     ← 项目简介
├── docker-compose.yml            ← 开发环境(前后端)
├── docker-compose.prod.yml       ← 生产环境(NAS部署,含PostgreSQL)
├── .env.prod.example             ← 生产环境配置模板
├── .gitignore
│
├── backend/                      ← FastAPI 后端
│   ├── main.py                   ← 入口(lifespan建表)
│   ├── config.py                 ← 配置(环境变量)
│   ├── db.py                     ← 数据库连接 + Session
│   ├── Dockerfile                ← 后端镜像
│   ├── requirements.txt
│   ├── sync_kol_to_db.py         ← KOL 数据同步脚本(从CSV导入DB)
│   │
│   ├── models/                   ← 6 张数据表
│   │   ├── kol.py                ← KOL 主表
│   │   ├── thread.py             ← 邮件会话(聚合往来)
│   │   ├── message.py            ← 邮件消息(含 AI 分析 JSON)
│   │   ├── operator.py           ← 运营人员
│   │   ├── note.py               ← 内部备注
│   │   └── send_log.py           ← 发送日志
│   │
│   ├── api/                      ← REST 接口
│   │   ├── kol.py                ← KOL CRUD + CSV导入 + 生成开场白
│   │   ├── threads.py            ← 会话看板/分配/详情
│   │   ├── webhook.py            ← ⭐ 接收 Snov 已发/回信 webhook
│   │   ├── operators.py          ← 运营人员管理
│   │   └── stats.py              ← 统计
│   │
│   └── services/                 ← 业务逻辑
│       ├── ai_intent.py          ← ⭐ AI 意向分析(核心,GPT打分)
│       ├── ai_personalize.py     ← GPT 生成个性化开场白
│
├── frontend/                     ← Vue3 前端
│   ├── Dockerfile                ← 前端镜像(nginx)
│   ├── nginx.conf                ← nginx 配置(SPA+API反代)
│   ├── vite.config.js            ← 开发代理
│   └── src/
│       ├── main.js               ← 入口
│       ├── App.vue
│       ├── api/index.js          ← axios 封装
│       ├── router/index.js       ← 路由
│       ├── layouts/MainLayout.vue← 侧边栏布局
│       └── views/                ← 6 个页面
│           ├── Dashboard.vue     ← 总览
│           ├── HotLeads.vue      ← ⭐ Hot Lead 看板(按意向分排序)
│           ├── ThreadDetail.vue  ← 会话详情+邮件往来+AI面板+回复
│           ├── KolList.vue       ← KOL 列表+批量生成开场白+推送发信
│           ├── KolImport.vue     ← CSV 导入
│           └── Stats.vue         ← 统计
│
└── docs/                         ← 文档
    ├── NAS_DEPLOY.md             ← 飞牛 NAS 部署指南
    ├── INSTANTLY_SETUP.md        ← Instantly 配置指南
    ├── INSTANTLY_STEP_BY_STEP.md ← Instantly 保姆级操作教学
    └── PREREQUISITES.md          ← 第三方账号准备清单
```

---

## 四、当前完成度

### ✅ 已完成并验证

```
1. 项目骨架(FastAPI + Vue3 + Docker)        已跑通
2. 数据库建模(6张表)                         已验证
3. KOL CSV 导入接口                           已验证(38+194=232个KOL已入库)
4. GPT 个性化开场白生成(ai_personalize.py)   已实现
5. AI 意向分析(ai_intent.py)                 已实现+联调通过
6. Webhook 接收端点(同步 Snov 已发/回信)       已实现
7. Snov API 接入(Client ID/Secret 有效)      已验证
8. Snov 数据导入(38 Dola + 175 Mango)         已完成
9. Hot Lead 看板前端                          已实现
10. 会话详情 + AI 面板 + 回复框               已实现
11. 运营人员管理(分配/备注)                   已实现
12. 生产 Docker Compose(NAS 部署版)          已准备
13. 飞牛 NAS 部署文档                         已写好
```

### ⏳ 进行中 / 待完成

```
1. ⏳ NAS 部署
   → 中台还没部署到飞牛 NAS(用户在准备 SSH)
   → 部署文档: docs/NAS_DEPLOY.md
   → 部署后需重新导入 KOL 数据到 PostgreSQL

2. ⏳ Snov webhook 订阅
   → 中台部署 + HTTPS 通了之后,用 Snov API 订阅 reply 事件
   → POST /v1/webhooks, url 指向 NAS 域名

3. ⏳ Snov 发信与人工跟进(用户侧操作)
   → 用户在 Snov 网页创建 Campaign、发信和人工回复
   → 中台只同步会话与筛选意向

4. 🔧 待开发:多租户改造
   → 加 client_id 字段(支持多客户隔离)
   → 客户独立看板 + 计费报表

5. 🔧 待开发:客户看板(白标)
   → 给客户独立登录入口,只看自己的 Hot Lead

6. 🔧 待开发:计费模块
   → 按 Hot Lead 数/发送量统计,出月度账单

7. ✅ 中台不代发邮件
   → 运营始终在 Snov 人工回复；中台仅作会话与意向看板
```

---

## 五、关键配置信息

### 5.1 Snov.io API

Snov API 凭据不得写入仓库或交接文档。请仅通过 NAS 的 `.env.prod` 配置，并在任何曾暴露凭据的渠道后立即轮换。当前中台的核心链路只依赖 `SNOV_WEBHOOK_TOKEN` 接收 webhook。

### 5.2 Snov API 端点(实测可用)

```
✅ POST /v1/oauth/access_token        ← 拿 token
✅ GET  /v1/get-balance               ← 查余额(5000积分)
✅ GET  /v1/get-user-campaigns        ← 查 Campaign 列表
✅ GET  /v1/get-user-lists            ← 查收件人列表
✅ POST /v1/lists                     ← 创建列表
✅ POST /v1/add-prospect-to-list      ← 添加 KOL 到列表(注意是连字符!)

❌ Snov API 不支持(强制走网页):
   - 创建 Campaign
   - 写邮件内容
   - 启动 Campaign
   - 发送单封邮件
```

### 5.3 Snov 列表(已创建)

```
id=40237769 | Dola-UK-KOL-38    | 39 个  (Dola 客户)
id=40248815 | Mango-Dreamina    | 82 个  (Mango 客户)
id=40248982 | Mango-Pippit      | 50 个
id=40249142 | Mango-Kimi        | 28 个
id=40249143 | Mango-Dola        | 15 个
```

### 5.4 Webhook 接收端点

```
中台接收地址:
   POST /api/webhook/snov?token=WEBHOOK_TOKEN
   POST /api/webhook/instantly?token=WEBHOOK_TOKEN

webhook 逻辑(backend/api/webhook.py):
1. 验证 token
2. 按 email 找 KOL
3. 幂等(message_id 去重)
4. 归组(In-Reply-To 匹配已有 thread)
5. 写 message 表
6. 调 ai_intent.py 做 GPT 分析
7. 更新 thread 的 intent/score/status
```

### 5.5 AI 意向分析逻辑(backend/services/ai_intent.py)

```
Prompt 让 GPT 输出 JSON:
{
  "intent": "high|medium|low|negative|ooo|auto_reply",
  "intent_score": 0-100,
  "budget_mentioned": "金额或null",
  "key_questions": [...],
  "timeline": "urgent|flexible|none",
  "summary": "中文一句话总结",
  "suggested_action": "立即跟进|温和跟进|暂不跟进|放弃"
}

阈值映射:
- high 或 score≥75 → thread.status='hot'(推到看板顶部)
- 40-74 → 'warming'
- <40 → 'open'
- negative → 'closed'

无 OPENAI_API_KEY 时走关键词规则兜底。
```

---

## 六、邮件文案(已定稿,待发)

### 6.1 Dola 客户(38个KOL,3封序列)

```
第1封主题: ✨ Paid YouTube Collaboration with Dola
第2封主题: re: ✨ Paid YouTube Collaboration with Dola (+3天)
第3封主题: re: ✨ Paid YouTube Collaboration with Dola (+7天分手信)
第4封: 打开未回(条件触发,等5天,轻量留台阶版)

文案见对话记录,核心信息:
- Dola 是字节(TikTok母公司)的 AI 助手
- 邀请做 YouTube 视频
- 交付:1专属视频+30天bio+90天ads+usage rights
- 团队署名 Richup Media(不用个人名,因为是10个Gmail轮发)
```

### 6.2 Mango 客户(175个KOL,A/B测试)

```
第1封A版: 详细产品介绍(Pippit/Dreamina/Kimi三产品)
第1封B版: 简化版 + faceless内容提议(discounted publishing-only rate)
第2-4封: 同 Dola 结构

文案已在对话中定稿,需要粘进 Snov Campaign。
```

---

## 七、本地开发环境

### 7.1 启动后端

```bash
cd kol-outreach/backend
pip install -r requirements.txt

# 配置环境(可选,不配也能跑,AI走兜底)
copy .env.example .env  # 编辑填 OPENAI_API_KEY

python -m uvicorn main:app --reload --port 8000
```

### 7.2 启动前端

```bash
cd kol-outreach/frontend
npm install
npm run dev
# 访问 http://localhost:5173
```

### 7.3 依赖版本

```
Python 3.12.10
Node 20+
FastAPI 0.139.0
SQLAlchemy 2.0.51
Vue 3.4
Ant Design Vue 4.1
```

---

## 八、给接手 AI 的工作建议

### 优先级 1:NAS 部署 + webhook 接通(卡住的一环)

```
现状: 中台只在本地跑过,webhook 没接通
任务: 
  1. 协助用户在飞牛 NAS 部署中台(docs/NAS_DEPLOY.md)
  2. 配 HTTPS + 反向代理
  3. 用 Snov API 订阅 webhook:
     POST https://api.snov.io/v1/webhooks
     {event: "replied", url: "https://kol.域名/api/webhook/snov?token=xxx"}
  4. 测试全链路
```

### 优先级 2:Snov 发信深度集成

```
现状: send.py 的 push-campaign 用的是 Instantly 格式
任务:
  1. 适配 Snov API 格式(Snov 没有直接发信API,但可以加lead到campaign)
  2. 实现运营在 ThreadDetail 点"回复"→ 通过 Snov 真发信
     (Snov 可能需要走 SMTP,因为 API 不支持发单封)
```

### 优先级 3:多租户改造(代理商核心)

```
现状: 单租户,所有 KOL 在一起
任务:
  1. kol/thread/message 表加 client_id 字段
  2. API 加 client 过滤
  3. 前端加"客户切换"
  4. 加客户独立看板(白标登录)
  5. 加计费统计模块
```

### 重要注意事项

```
1. 网络问题: 用户电脑的代理会拦截 API 请求
   → 任何 Snov API 调用必须设置 NO_PROXY=* 和 requests.Session.trust_env=False
   → 参见 backend/scripts 下的脚本

2. 代理拦截: 用户本地跑 API 脚本时,requests 默认走代理会被拦
   → 解决: os.environ['NO_PROXY']='*' + Session.trust_env=False

3. Snov API 端点不规律:
   → /v1/oauth/access_token(下划线)
   → /v1/add-prospect-to-list(连字符)
   → /v1/get-user-lists(连字符)
   → 不要假设命名规律,实测为准

4. 数据安全: Snov Client ID/Secret 已在对话中明文暴露,建议提醒用户重置
```

---

## 九、文件清单(打包内容)

```
✅ 全部源码(backend + frontend)
✅ Docker 配置(开发+生产)
✅ 文档(docs/ + README + HANDOFF)
✅ 配置模板(.env.example)

❌ 不含(已 .gitignore / 清理):
   - node_modules
   - __pycache__
   - .env(真实密钥)
   - *.db 数据库文件
   - dist 构建产物
```

---

## 十、快速验证(接手后第一件事)

```bash
# 1. 解压
unzip kol-outreach.zip && cd kol-outreach

# 2. 启动后端
cd backend && pip install -r requirements.txt
python -m uvicorn main:app --port 8000

# 3. 测试健康
curl http://localhost:8000/health
→ {"status":"ok"}

# 4. 测试 Snov API 连通
python -c "
import os; os.environ['NO_PROXY']='*'
import requests
s=requests.Session(); s.trust_env=False
r=s.post('https://api.snov.io/v1/oauth/access_token',json={
  'grant_type':'client_credentials',
  'client_id':'2b523c73b52016855796a2192e2191c1',
  'client_secret':'a9dce4df5ce2f8bffda3b74c9172faaa'
})
print('Snov API:', 'OK' if r.status_code==200 else 'FAIL')
"

# 5. 测试 AI 分析(需配 OPENAI_API_KEY,否则走规则兜底)
curl -X POST http://localhost:8000/api/webhook/snov?token=change-me-in-production \
  -H "Content-Type: application/json" \
  -d '{"event":"replied","email":"test@example.com","message":"Interested! Send pricing."}'
```

---

**文档结束。接手 AI 有疑问,参考对话记录中的完整开发过程。**
