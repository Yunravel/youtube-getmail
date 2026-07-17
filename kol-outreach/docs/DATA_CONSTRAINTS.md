# 数据约束文档（DATA_CONSTRAINTS）

> **目的**：约束数据库设计和代码改动——每个字段必须明确"数据从哪来、谁来填、什么时候填、填不了怎么办"。
> 避免凭印象建列，避免"建了列但永远空"的设计债。
>
> **状态图例**：✅ 已确认 ｜ ⚠️ 部分确认，需补充 ｜ ❓ 待补充（用户/实测填）
>
> **最后更新**：2026-07-17 ｜ **Snov 官方文档源**：https://snov.io/api

---

## 0. 数据来源全景（三源分立）

系统存在**三个互不重叠的数据世界**，数据库设计必须按数据源隔离，不能假设某字段所有来源都有：

| 数据源 | 进入系统的途径 | 覆盖的联系人 | 数据特点 |
|--------|--------------|------------|---------|
| **Snov API** | `snov_contacts.py` 同步 prospect-list | 纯 Snov 联系人 | 只有 Snov 返回的字段 |
| **Snov webhook/轮询** | `webhook.py` + `snov_scheduler.py` | 收到回信的联系人 | 邮件会话、AI 意向 |
| **Excel 采集表** | `import_kol_xlsx.py` 导入 | Excel 批次内的联系人 | 达人画像、采集元数据、适配评估 |

**实测交叉（2026-07-17，283 行）**：
```
71 行：Snov + Excel 双源（字段较全）
7 行：仅 Excel（未进 Snov 名单）
204 行：仅 Snov（无 Excel 画像数据）
1 行：两源都没有
```

**设计含义**：Excel 专属字段（如粉丝数、适配评估）若建在 `kol` 主表，204 个纯 Snov 联系人该列永远为 NULL。应考虑放在可选的子表（无记录=未采集），而非主表列（NULL=歧义）。

---

## 1. Snov API 能力清单

> 以下基于 Snov 官方 API 文档（https://snov.io/api）+ 项目代码 `snov_client.py` / `snov_contacts.py` 实际消费的字段。

### 1.1 `prospect-list`（读取名单内联系人）✅

**端点**：`POST /v1/prospect-list`（代码 `snov_client.py:115`）

**⚠️ 官方文档宣称返回 vs 本账号实测返回（2026-07-17，275 行 raw_data）**：

Snov 官方文档列了许多字段，但**本账号实际只返回下面 6 个顶层字段**（`snov_raw_data` 列实测）：

| 字段 | 类型 | 实测覆盖率 | 状态 |
|------|------|-----------|------|
| `id` | string | 275/275 (100%) | ✅ 实测确认 |
| `name` | string | 275/275 (100%) | ✅ 实测确认 |
| `firstName` | string | 275/275 (100%) | ✅ 实测确认 |
| `lastName` | string | 275/275 (100%，可空) | ✅ 实测确认 |
| `source` | string | 275/275 (100%) | ✅ 实测确认 |
| `emails[]` | array | 275/275 (100%) | ✅ 实测确认 |

**`emails[]` 数组内每个对象的字段**（实测 16 个，全是邮箱验证类）：
`email`、`probability`、`isVerified`、`smtpStatus`、`statusTypeText`、`domainType`、`jobStatus`、`isValidFormat`、`isDisposable`、`isWebmail`、`isGibberish`、`isCatchall`、`isGreylist`、`isConnectionError`、`isBannedError`、`emailVerifyText`、`statusVerifyText`

**🔴 官方文档宣称返回、但本账号实测 0 行的字段**（`snov_contacts.py` 代码映射了，但 Snov 实际没给）：

| 代码映射的字段 | DB 列 | Snov 来源非空行数 | 结论 |
|--------------|-------|-----------------|------|
| country | `country` | **0**（71 行非空全是 Excel 填的） | 🔴 Snov 不返回 |
| position | `position` | 0 | 🔴 Snov 不返回 |
| companyName | `company_name` | 0 | 🔴 Snov 不返回 |
| locality | `locality` | 0 | 🔴 Snov 不返回 |
| phones | `phones` | 0 | 🔴 Snov 不返回 |
| socialLinks.linkedIn | `linkedin_url` | 0 | 🔴 Snov 不返回 |
| customFields | `snov_custom_fields` | **283 行全 NULL** | 🔴 本账号未配 customFields |

> **根因**：`snov_contacts.py` 的 `_mapped_fields()` 按官方文档写了 country/position/company 等的映射，但本账号的 Snov 订阅/数据源不返回这些。**这些列在纯 Snov 联系人里永远是 NULL**，是当前 kol 表 36 列里大量空值的直接原因。

**本账号 customFields 状态**：✅ 已确认未配置任何 customFields（283 行 `snov_custom_fields` 全为 NULL）。因此 `platform`、`followers`、`priority` 等字段**无法从 Snov 获取**，只能靠 Excel。

### 1.2 `get-emails-replies`（读取 Campaign 回信）✅

**端点**：`GET /v1/get-emails-replies`（代码 `snov_client.py:133`）

**确定返回的字段**：

| 字段 | 类型 | 说明 | 状态 |
|------|------|------|------|
| `prospectName` | string | 联系人名 | ✅ |
| `prospectEmail` | string | 联系人邮箱 | ✅ |
| `prospectId` | string | prospect ID（⚠️ 每次读取会变，不能做幂等键） | ✅ 代码已处理 |
| `emailSubject` | string | 邮件主题 | ✅ |
| `emailBody` | string | 邮件正文 | ✅ |
| `receivedAt` / `timestamp` | string/int | 接收时间 | ✅ |
| `customFields` | object | 自定义字段 | ⚠️ |

**不返回的字段**（重要限制）：

| 字段 | 状态 | 说明 |
|------|------|------|
| **附件 attachments** | ❌ 不返回 | README 已说明；需接 IMAP/Gmail API/Graph 才能拿全附件 |
| **Message-ID 邮件头** | ❌ 不保证返回 | 代码用 sha256 指纹兜底（`webhook.py:164`） |
| **In-Reply-To / References** | ❌ 不返回 | RFC5256 归组字段无法靠 Snov 填充 |
| **发件人 from / 收件人 to** | ⚠️ 部分返回 | 代码多处 fallback（`webhook.py:109`） |

### 1.3 `customFields` 机制（逃生舱）⚠️

`customFields` 是 Snov 的"用户自定义字段"——**你在 Snov 后台或通过 API 配了什么，它就返回什么**。

**未确认的关键问题**：

- ❓ **本 Snov 账号当前配置了哪些 customFields？**
  - 影响判断：`platform`、`followers`、`priority` 等字段在纯 Snov 联系人里能否被填充
  - 验证方法：`GET /v1/fields`（"Find Prospect's Custom Fields" 端点）或查数据库 `snov_custom_fields` JSON 列的实际 key
- ❓ **导入 Excel 时，是否把 platform/followers 等写回了 Snov 的 customFields？**
  - 当前代码 `snov_contacts.py` 只**读** customFields，没有**写**回去的逻辑

---

## 2. Excel 采集表字段清单

> 基于实际文件 `Pippit_KOL_100_2026-07-14_邮箱采集结果1(1).xlsx`（100 行 × 23 列）。

### 2.1 字段全表（23 列）

| # | Excel 列名 | 示例值 | 导入脚本当前处理 | 应归属 |
|---|-----------|--------|----------------|--------|
| 1 | 平台 | YouTube/Twitter-X/Instagram | → `platform` | kol |
| 2 | 账号 | @HermanHuang | → `social_handle` | kol |
| 3 | 昵称 | Herman Huang | → `name`+`full_name` ⚠️双写 | kol |
| 4 | 主页链接 | youtube.com/@... | → `profile_url`+`channel_url` ⚠️双写 | kol |
| 5 | 粉丝数 | 188000 | → `followers`+`subscribers` ⚠️双写 | kol/kol_metric |
| 6 | 10天平均浏览量 | 6266 | 🔴 塞进 `contact_notes` | kol_metric ❓ |
| 7 | 国家/地区 | United States | → `country` | kol |
| 8 | 语言 | English | 🔴 塞进 `contact_notes` | kol ❓ |
| 9 | 达人类别 | 影视创作与视频生产力达人 | 🔴 塞进 `contact_notes` | kol_sourcing ❓ |
| 10 | 内容赛道 | 视频后期工具与 AI 生产力对比 | → `content_category`+`niche` ⚠️双写 | kol |
| 11 | 适配Pippit | ✓ | 🔴 塞进 `contact_notes` | kol_sourcing ❓ |
| 12 | 社会身份/头衔备注 | 导演；影视制作人 | 🔴 塞进 `contact_notes` | kol_sourcing ❓ |
| 13 | 推荐内容角度 | 做"传统后期流程 vs AI"对比 | 🔴 塞进 `contact_notes` | kol_sourcing ❓ |
| 14 | 主要推荐产品 | Pippit | 🔴 塒进 `contact_notes` | kol_sourcing ❓ |
| 15 | 内容证据 | How Filmmakers Should... | ⚠️ 降级为 `recent_videos[0]` | kol_content ❓ |
| 16 | 来源链接 | youtube.com/@... | 🔴 塞进 `contact_notes` | kol_sourcing ❓ |
| 17 | 数据更新时间 | 2026-07-14 | 🔴 塞进 `contact_notes` | kol_metric/sourcing ❓ |
| 18 | 联系邮箱 | xxx@gmail.com（可能含 \|） | → `email` | kol_email ❓ |
| 19 | 邮箱状态 | 已获取/需人工验证/未发现 | 🔴 塞进 `contact_notes` | kol_email ❓ |
| 20 | 邮箱来源 | 公开主页简介/官网URL | 🔴 塞进 `contact_notes` | kol_email ❓ |
| 21 | 公开外链 | xxx.com | → `company_site` | kol |
| 22 | 采集状态 | 成功/需要登录验证 | 🔴 塞进 `contact_notes` | kol_sourcing ❓ |
| 23 | 采集时间 | 2026-07-14 23:49 | 🔴 塒进 `contact_notes` | kol_sourcing ❓ |

### 2.2 邮箱状态取值分布（实测 100 行）

| 取值 | 数量 | 占比 |
|------|------|------|
| 未发现 | 42 | 42% |
| 需人工验证（登录/reCAPTCHA） | 26 | 26% |
| 已获取；另有需人工验证的商务邮箱 | 25 | 25% |
| 已获取 | 7 | 7% |

### 2.3 多邮箱情况

- 34/100 行有邮箱
- **6 行有多个邮箱**（用 `|` 分隔）
- 当前 `kol.email` 单列无法存多邮箱，多出的塞进 `contact_notes`

---

## 3. 字段来源决策矩阵（设计核心）

> 针对每个候选字段，回答：**这个字段谁能填？** 决定它该放哪。

| 候选字段 | Snov prospect | Snov reply | Excel | 决策 |
|---------|:---:|:---:|:---:|------|
| email | ✅ | ✅ | ✅ | kol_email 表（多源共用） |
| full_name / name | ✅ | ✅ | ✅ | kol 主表（单一权威，去冗余） |
| firstName / lastName | ✅ | ✅ | — | kol 主表（开场白用） |
| country | 🔴实测0行 | — | ✅ | kol 主表（实际只有 Excel 填） |
| platform | 🔴customField未配 | — | ✅ | kol 主表（实际只有 Excel 填） |
| followers | 🔴customField未配 | — | ✅ | kol 主表（实际只有 Excel 填） |
| position/company/phones/linkedin | 🔴实测0行 | — | — | 🔴考虑删除映射或保留待用 |
| avg_views_10d | ❌ | ❌ | ✅ | kol_sourcing（Excel 专属） |
| fit_assessment | ❌ | ❌ | ✅ | kol_sourcing（Excel 专属） |
| content_angle | ❌ | ❌ | ✅ | kol_sourcing（Excel 专属） |
| email_status | ❌ | ❌ | ✅ | kol_email（采集元数据） |
| collection_status | ❌ | ❌ | ✅ | kol_sourcing（采集溯源） |
| last_intent | — | ✅AI算 | — | thread 表 |
| ai_analysis | — | ✅AI算 | — | message 表 |
| attachments | ❌ | ❌ | — | message 表（基本空） |

---

## 4. 待你补充的清单（我拿不准的）

以下是我无法单方面确认、需要你提供或实测的：

### 4.1 Snov 账号配置类

- [x] **本 Snov 账号配置了哪些 customFields？** → ✅ **已实测：未配置任何 customFields**（283 行 `snov_custom_fields` 全 NULL）。影响：`platform`/`followers`/`priority` 等无法从 Snov 获取，只能靠 Excel。
- [x] **Snov 实际返回哪些顶层字段？** → ✅ **已实测：仅 6 个**（`id`/`name`/`firstName`/`lastName`/`source`/`emails`）。country/position/company/locality/phones/linkedin/customFields 本账号 0 行返回。
- [x] **Excel 导入时，是否需要把 platform/followers/fit 等回写到 Snov customFields？** → ✅ **不做回写**。Snov 只当发信通道，画像数据留本地。依据：本账号 customFields 未配置，且回写需在 Snov 后台建字段定义 + 新增写回代码，ROI 不足。

### 4.2 业务规则类

- [x] **本平台定位** → ✅ **只做中转，暂不考虑发送功能**（发信仍在 Snov）。影响：send_log 表无实际写入路径，mailbox 的 bounced 文件夹长期为空，可考虑废弃 send_log。
- [x] **多邮箱发信语义** → ✅ **只发主邮箱**。主邮箱优先级 = 个人邮箱 > 商务邮箱 > 经纪邮箱；公共支持邮箱（`help@skool.com` 类）永不自动发，标 `risky`。`contact_point.is_primary` 仅一个，发信名单只取 `validation_status=valid` 且非 `risky/suppressed` 的主联系点。（注：当前平台不发信，此规则作为 `contact_point` 模型的语义基准，供未来或外部发信时启用。）
- [x] **"邮箱状态=未发现"的 KOL 是否入库** → ✅ **入库**。KOL 有身份（kol_entity 有行），允许零联系点；标记 `contact_discovery_status=not_found`。无记录=未采集，优于 NULL。不单独建"待采集池"——标记本身就是池子。
- [x] **回信评价放哪** → ✅ **不建独立表**。意愿/报价/时间线是 AI 分析的输出，天然属于某封回信（存 `message.ai_analysis`），thread 汇总会话级意愿（`thread.intent_score`/`last_intent`）。详见 §7 表结构。
- [x] **KOL 状态机的合法转移路径** → ✅ `pending→sent→in_conversation→closed` **单向**；`blacklisted` 可由**人工**恢复为 pending（不自动）。lifecycle 状态与发信状态分离：`kol_entity.lifecycle_status`（active/inactive/blocked/merged）管身份，`thread.status` 管会话。

### 4.3 数据时效类

- [x] **粉丝数/浏览量是否保留历史** → ✅ **保留两个版本：入表时快照 + 当前值**。
  - 实现方式：新增 `kol_metric_snapshot` 表，每次导入/同步插入带时间戳的快照行
  - `kol.followers` 存当前值（最新覆盖），snapshot 存历史
  - **不主动调外部 API 刷新**（见下条"当前数据获取难度"）
- [x] **"当前数据"的获取难度** → ⚠️ **分两档**：
  - **低难度**：当前值 = 最近一次导入/同步的值（已有数据，只需存快照）→ **先做这个**
  - **高难度**：当前值 = 实时调 YouTube/Instagram API 查最新粉丝数 → 需集成第三方平台 API（配额/token/速率限制），**推迟到采集工具集成后**
- [x] **Excel 多批次导入的取舍规则** → ✅ **重复部分按最新一次导入为准，非重复部分保留**。
  - 当前代码 `merge_duplicate` 取 `max(followers)` 的逻辑需改为：**重复邮箱以最新批次数据覆盖**（而非 max）
  - 注意：`contact_notes`/`source` 等历史溯源字段应**追加**而非覆盖（保留来源轨迹）

### 4.4 外部系统类

- [x] **附件接入** → ✅ **保留接入 IMAP/Gmail API 拿附件的可能性**。`message.attachments` 列保留，但已知 Snov reply API 不返回附件（§1.2），当前基本为空。
- [x] **采集工具集成** → ✅ **计划集成进系统**，但需遵守 §8 的爬虫集成约束（独立服务、独立 IP、异步任务、清洗层）。当前 `D:/mail/scripts/*.mjs` 是独立 Node 脚本，字段见 §2。

---

## 5. 已确立的设计原则（基于以上约束）

1. **按数据源隔离**：Excel 专属字段不进 kol 主表，放可选子表（无记录=未采集，优于 NULL=歧义）
2. **多值字段独立成表**：多邮箱（kol_email）、多内容素材（kol_content）不塞 JSON
3. **冗余字段单一权威**：name/full_name、subscribers/followers、niche/content_category、channel_url/profile_url 这 4 对必须各保留一个，另一个删除或用生成列。多批次导入取舍：**指标（followers/views）一律写新快照，永不覆盖**；**定性字段（达人类别/内容赛道/适配评估）以最新批次为准覆盖**；`source`/`contact_notes` 等溯源字段**追加**而非覆盖。
4. **枚举字段加约束**：status、direction、email_status、priority 用 CHECK 或 ENUM，拒绝自由字符串
5. **email 不再是 KOL 行的单值属性**：采用 `contact_point` + `kol_contact_point` 关联表（见 PIPPIT §4.4）。KOL 允许零联系点入库（无记录=未采集）。旧 `kol.email` 单列在迁移期保留为兼容投影，最终收缩。发信名单只取 `validation_status=valid` 且未 bounced/blocked/suppressed 的主联系点。
6. **外键加 ON DELETE CASCADE**：删 KOL 级联删其 thread/message/note，修复 delete_kol 报错 bug
7. **不改 Snov 原始数据**：`snov_raw_data` JSON 列保持原样，作为回溯兜底
8. **退订/拒联走全局 suppression**：用 `contact_point.deliverability_status`（含 bounced/blocked）+ 一张全局 `suppression_list`；退订即拉黑；暂不设自动删除期限（GDPR/CAN-SPAM 的自动清除留作后续合规需求）。
9. **KOL 主体边界**：`kol_entity.entity_type` 先只建 `person` / `organization` 两类。频道/媒体一律视为 person 名下的 `social_account`，不单独建类型（YAGNI，等真有组织型客户再扩）。
10. **旧 API 兼容**：`kol` 表迁移期保留为兼容映射表至少一个发布周期，不急着转视图；转视图是不可逆收敛动作，留到业务稳定后。

---

## 7. 目标表结构（基于已确认决策）

> 数据流：Excel 采集 → kol + kol_email + kol_metric_snapshot 入库；Snov 回信 → thread + message + ai_analysis。
> 平台只中转不发信，因此**无 send_log 写入路径**，send_log 表考虑废弃。

### 7.1 表清单与职责

| 表 | 职责 | 状态 | 谁来填 |
|----|------|------|-------|
| `kol` | 人是谁：身份 + 画像 + 状态 | 改造（精简冗余列） | Excel + Snov 同步 |
| `kol_email` | 怎么联系：多邮箱 + 采集状态 | **新增** | Excel + 回信自动补入 |
| `kol_metric_snapshot` | 数据时效：入表快照 + 当前值 | **新增** | 导入/同步时写 |
| `thread` | 会话线索：1 KOL × 1 Campaign | 已存在 | webhook 创建 |
| `message` | 每封邮件 + AI 评价 | 已存在 | webhook + AI |
| `note` / `operator` | 运营协作 | 已存在（空） | 手动 |

### 7.2 `kol` 表改造方向（去冗余，单一权威）

删除 4 对冗余里的旧字段，保留新字段为单一权威：

| 冗余对 | 保留 | 删除/弃用 |
|--------|------|----------|
| `name` / `full_name` | `full_name`（Snov API 字段名） | `name`（或用生成列 `GENERATED ALWAYS AS` 派生） |
| `channel_url` / `profile_url` | `profile_url` | `channel_url` |
| `subscribers` / `followers` | `followers` | `subscribers` |
| `niche` / `content_category` | `content_category` | `niche` |

> ⚠️ 删除前需改所有读写代码（webhook.py 的 kol.name、mailbox.py 的 Kol.name 查询、kol.py 的 CSV 映射等）。
> 过渡期可用视图 `CREATE VIEW kol_view AS SELECT full_name AS name, ...` 兼容旧查询。

### 7.3 `kol_email`（新增，多邮箱 + 采集元数据）

```sql
kol_email
  id            PK
  kol_id        FK → kol(id) ON DELETE CASCADE
  email         VARCHAR(200) NOT NULL
  is_primary    BOOLEAN DEFAULT false      -- 供未来发信用，当前不发信可全 false
  -- 采集元数据（Excel 专属，Snov 回信补入时这些为空）
  status        VARCHAR(50)                -- acquired/needs_verification/not_found（枚举化）
  source        VARCHAR(300)               -- 邮箱来源（公开主页简介/官网URL/...）
  collected_at  TIMESTAMP
  created_at    TIMESTAMP DEFAULT now()
  UNIQUE(lower(trim(email)))               -- 全局邮箱去重（替代 kol 表的部分索引）
  INDEX(kol_id)
```

**关键设计**：
- `email` 唯一约束落到这张表，`kol` 表不再存单个 email（改存 `primary_email_id` 反查，或完全靠 JOIN）
- 未发现邮箱的 KOL：`kol` 有行，`kol_email` 无行（语义清晰）
- 多邮箱：一个 KOL 多行（解决 Excel 6/100 的多邮箱问题）
- Snov 回信进来时，若回信邮箱不在 `kol_email`，自动补一行（`source='reply'`）

### 7.4 `kol_metric_snapshot`（新增，数据时效）

```sql
kol_metric_snapshot
  id            PK
  kol_id        FK → kol(id) ON DELETE CASCADE
  metric_type   VARCHAR(30)                -- followers / avg_views_10d / engagement
  value         INTEGER
  recorded_at   TIMESTAMP                  -- 数据更新时间（来自 Excel "数据更新时间"）
  source        VARCHAR(200)               -- 哪次导入/同步
  INDEX(kol_id, metric_type, recorded_at)
```

**使用方式**：
- 当前值：`kol.followers`（最新覆盖）或 `SELECT value ... ORDER BY recorded_at DESC LIMIT 1`
- 入表时值：`SELECT value ... ORDER BY recorded_at ASC LIMIT 1`
- **不主动刷新**（§4.3 已确认），等采集工具集成后再升级

### 7.5 回信评价的去处（不建独立表）

意愿/报价/时间线等 AI 评价，**继续存在现有位置**：
- `message.ai_analysis` JSON（每封回信的分析，已存在）—— 含 intent_score/budget_mentioned/timeline/key_questions
- `thread.intent_score` / `thread.last_intent` / `thread.ai_summary`（会话级汇总，已存在）

**不需要新增"回信评价表"**——评价天然属于某封 message，不存在脱离邮件的评价。

---

## 8. 爬虫（采集工具）集成约束

> 用户计划把 `D:/mail/scripts/*.mjs` 采集工具集成进系统。以下为必须遵守的约束。

### 8.1 四类隐患与对策

| 隐患 | 严重度 | 对策 |
|------|--------|------|
| **法律合规**（平台 ToS 禁止自动化采集 + CAN-SPAM/GDPR） | 🔴 高 | 爬虫用独立 IP/代理池，不与中台共享出口 IP；只采公开联系方式 |
| **技术稳定性**（反爬：改版/登录态/CAPTCHA/限流） | 🟡 中 | 爬虫做异步任务（Celery/RQ），失败重试 + 死信队列，不阻塞主系统 |
| **数据质量**（脏值：Excel 国家字段有 "Slop City, Mars"/"👉"） | 🟡 中 | 需清洗层，导入前校验枚举值（如国家应匹配 ISO 列表） |
| **架构耦合**（Playwright/浏览器依赖会让后端镜像膨胀、崩溃拖垮 API） | 🟡 中 | 爬虫作为独立服务，通过 DB 或消息队列与中台解耦 |

### 8.2 集成时的硬性要求

1. **进程隔离**：爬虫不跑在 FastAPI 进程内，独立进程/容器
2. **IP 隔离**：爬虫出口 IP ≠ 中台 webhook 接收 IP（避免连坐封禁）
3. **写入接口统一**：爬虫只写 `kol` + `kol_email` + `kol_metric_snapshot`，通过与 Excel 导入相同的清洗层（复用 `import_kol_xlsx.py` 的 `clean_row` 逻辑）
4. **字段稳定性契约**：爬虫产出的字段集变更时，必须同步更新 §2 的 Excel 字段表和本约束文档
5. **速率限制**：对目标平台的请求必须限流（避免触发 ban）

### 8.3 当前采集工具的证据（失败率）

从 Excel 实测：`采集状态` 74 成功 / 26 需登录验证 = **26% 失败率**。集成前需先把失败率降到可接受水平，否则持续运行会持续产出半成品数据。

---

## 9. 变更记录

| 日期 | 变更 | 依据 |
|------|------|------|
| 2026-07-17 | 初版，基于 Snov 官方 API 文档 + Excel 实测 + DB 283 行实据 | https://snov.io/api |
| 2026-07-17 | 补充实测：Snov 本账号实际只返回 6 个顶层字段，country/position/company/customFields 均 0 行；customFields 未配置 | DB `snov_raw_data` / `snov_custom_fields` 实测 |
| 2026-07-17 | 用户决策回填：平台只中转不发信、未发现邮箱 KOL 入库、保留入表+当前两版本数据、最新批次覆盖重复项、计划集成采集工具 | 用户确认（本轮对话） |
| 2026-07-17 | 新增 §7 目标表结构（kol/kol_email/kol_metric_snapshot）+ §8 爬虫集成约束 | 业务决策 + 技术风险分析 |
| 2026-07-17 | 与 PIPPIT 标准化方案对齐：email 改为 contact_point 关联表（§5 原则 5 重写）；补回写 Snov=不做、状态机转移、主体边界、退订 suppression、旧 API 兼容期（§4 全部关闭 + §5 原则 8/9/10） | 用户"全部默认"批复 + PIPPIT §4.4/§12 |

---

> **下一步**：§4 全部决策已固化，§3 决策矩阵 + §5 原则 + §7 表结构可作为后续 schema 改动的约束基准。下一步将 §7 表清单与 PIPPIT 的 13 张表对齐收敛出第一版可执行 DDL（优先落地 PIPPIT 阶段 0–2）。
