# BUG 跟踪清单（BUG Tracker）

> 本文档是全项目 BUG 的**唯一登记处**。历史上 BUG 散落在交接文档、会话记录和记忆里，
> 已于 2026-07-27 统一收拢至此。此后**发现任何 BUG、修复任何 BUG，都必须同步更新本文档**。

## 更新规范（必读）

1. **发现即登记**：任何途径（测试、评审、生产事故、会话讨论）确认的 BUG，当天登记，
   状态置「❌未修复」。取全局递增编号（见文末「下一个可用编号」，用完 +1）。
2. **修复即改状态**：代码修好并测试通过 → 「🚀待部署」（若在独立分支则「🔀待合并」）；
   生产上线并验收 → 「✅已部署」。**改状态时同时更新「修复落点」列**（commit 或分支名）。
3. **状态枚举**（只用这几种）：
   | 标记 | 含义 |
   |---|---|
   | ❌未修复 | 已确认、代码未改 |
   | 🟡部分修复 | 改了但没改全，残留面写进备注 |
   | 🔀待合并 | 修复在独立分支，未进 `fix/db-kol-email-drift` 主干 |
   | 🚀待部署 | 已进主干（含测试），生产未上 |
   | ✅已部署 | 生产在跑且验收过 |
   | 📦数据/运营 | 非代码问题：数据清理、运营决策、流程规范 |
   | 🔍已证伪 | 曾被报告，核实后不成立（保留记录防止重复报告） |
4. **每条必填**：编号、一句话现象、位置（file:line 或模块）、状态、修复落点/备注。
5. **不删条目**：已修复/已证伪的条目留在表里，这是防止同类问题复发的知识库。
6. 大批量新发现（如一次深度测试）可新开一个批次小节，沿用全局编号。

## 未修复项速览（按建议优先级）

| 优先 | 编号 | 问题 | 状态 |
|---|---|---|---|
| 高 | BUG-037 | 三处唯一约束查后插并发竞争，修复已并入主干 | 🚀待部署 |
| 高 | BUG-009~012, 026 | 7/27 三 P0 + 时区换算批次，生产未部署（须带 alembic 0008，部署前勿触发 30 天补录） | 🚀待部署 |
| 高 | BUG-013 | IMAP 兜底匹配吞掉真实新回信（已修复待部署） | 🚀待部署 |
| 高 | BUG-014 | 前端把 UTC 当本地时间；定时发送每确认一轮提前 8 小时（已修复待部署） | 🚀待部署 |
| 中 | BUG-015 | /api/kols 列表泄漏 snov_raw_data 等内部字段 | ❌未修复 |
| 中 | BUG-031 | kol.email 无查重约束（双档根因） | ❌未修复 |
| 中 | BUG-033 | 本地 .env 仍存生产飞书表 token，只有注释约束 | ❌未修复 |
| 中 | BUG-018 | 非 ASCII 凭据/token 使认证路径 500 | ❌未修复 |
| 中 | BUG-021 | 无请求体 POST 可被跨站表单触发（send-now 真发信） | ❌未修复 |
| 中 | BUG-023 | 飞书任务 HTTP 阶段仍可被并发重复认领 | 🟡部分修复 |
| 中 | BUG-025 | 飞书对账周期性作废指数退避 | ❌未修复 |
| 低 | BUG-016 | 国家/赛道筛选子串匹配（Niger→Nigeria） | ❌未修复 |
| 低 | BUG-017 | 附件文件名含 `& ' + , ! [ ]` 存得进下不来 | 🟡部分修复 |
| 低 | BUG-019 | /api/feishu/audit 未配置时 500 | ❌未修复 |
| 低 | BUG-020 | /docs /openapi.json /redoc 匿名可访问（纵深缺口） | ❌未修复 |
| 低 | BUG-022 | since_days 传负数等同全量历史同步 | ❌未修复 |
| 低 | BUG-024 | 飞书 payload_hash 只写不读，「未变更跳过」从未生效 | ❌未修复 |
| — | BUG-030/032/034 | 数据清理与运营/流程决策项 | 📦数据/运营 |

---

## 批次 A：2026-07-26 「部分邮件数据获取不到」诊断

来源：`HANDOFF_20260726.md` §五/§六；基于 7/15–7/25 日志与数据库取证。

| 编号 | 问题 | 位置 | 状态 | 修复落点 / 备注 |
|---|---|---|---|---|
| BUG-001 | Snov webhook 从未接通：库内消息 100% 来自轮询，0 条 outbound | Snov 侧订阅缺失 | ✅已部署 | `scripts/subscribe_snov_webhooks.py` 一键订阅 3 事件，7/26 验收通过 |
| BUG-002 | 第三方地址真实回信（经纪人代回/客服系统）被 IMAP 只记日志丢弃 | `services/attachment_sync.py` | ✅已部署 | 7/26 未匹配落库 + 三道防垃圾闸门；7/27 imap-enrich 再完善（见 BUG-028） |
| BUG-003 | 轮询接口字段残缺：无收件邮箱（unknown@snov.local）、无附件、无邮件 ID | Snov API 限制 | ✅已部署 | 属第三方限制，IMAP 侧兜底修复未知收件人（7/26 验收修复 4 个）；附件仅 IMAP 可得 |
| BUG-004 | 内容指纹碰撞：同 KOL 两封同文回信第二封被误判 duplicate 丢弃 | `api/snov.py` | ✅已部署 | 批内相同指纹加序号 `…#1`；补录期漏网数据见 BUG-030 |
| BUG-005 | IMAP 代理链路瞬断（SSL EOF）导致整轮同步失败 | `services/imap_client.py` | ✅已部署 | 指数退避重试 `IMAP_CONNECT_RETRIES`（默认 3） |

## 批次 B：2026-07-26 安全与部署

来源：`HANDOFF_20260726.md` §九/§十二。

| 编号 | 问题 | 位置 | 状态 | 修复落点 / 备注 |
|---|---|---|---|---|
| BUG-006 | 生产 API 完全公开：/api/threads、/api/kols 无认证可读；`PUT /api/mailbox-credentials/{id}` 可改 IMAP 地址窃取邮箱密码 | `api/__init__.py` 全路由 | ✅已部署 | `api/auth.py` require_dashboard_auth 挂全路由（webhook 豁免），未配置 fail-closed 503；7/26 验收 401/200 通过 |
| BUG-007 | 部署漂移：scp 手写清单漏文件，8 文件不同步；旧版 quote_source_analysis 缺函数报错 15 次 | 部署流程 | ✅已部署 | 全树 sha256 比对后全量同步+重建，漏跑分析已补。**规范：部署前必须全树校验和比对** |
| BUG-008 | compose 未映射 DASHBOARD_* 环境变量，容器收不到 → 全站 503 | `docker-compose.prod.yml` | ✅已部署 | 已补映射 |

## 批次 C：2026-07-27 深度测试（P0/P1/P2）

来源：会话「项目深度测试」，全部经可执行测试实证。P0/P1-1/P2-9 的修复已随
commit `04bd21c` 基线进入 `fix/db-kol-email-drift`（199 项测试通过），**生产未部署**：
部署必须带 `alembic/versions/20260727_0008_received_at_provenance.py` 并执行
`alembic upgrade head`，完整文件清单见记忆 `kol-outreach-autoreply-broken-2026-07-27`；
**部署完成前不得触发 30 天补录**。

| 编号 | 级别 | 问题 | 位置 | 状态 | 修复落点 / 备注 |
|---|---|---|---|---|---|
| BUG-009 | P0 | 自动回复发送必崩：commit+close 后把 detached credential 交给 SMTP → DetachedInstanceError；异常逃逸中断整批；任务永久卡 sending | `services/auto_reply.py` send_due_task | 🚀待部署 | `SmtpCredentialSnapshot`（smtp_sender.py）commit 前拍快照；意外异常按 ambiguous 进 manual_review；批内单任务异常不断批；置 sending 改条件 UPDATE 原子抢占 |
| BUG-010 | P0 | 已发送/已取消任务被复活成 queued（FINAL_STATUSES 定义了但全仓库零引用）；运营手动取消/编辑被覆盖 | `services/auto_reply.py` _upsert_task | 🚀待部署 | evaluate 与 _upsert_task 双层守卫：终态/sending/运营接管（manual_override 或 edited_at）不覆写 |
| BUG-011 | P0 | 补录击穿 2 小时防误发闸门：日期解析失败回退 `utcnow()`（三条入库通道）判成「刚收到」；叠加报价识别不剥引用块 → 给一个月前拒绝的达人自动回「已收到报价」 | `api/webhook.py` / `api/snov.py` / `services/attachment_sync.py` | 🚀待部署 | 新列 `message.received_at_estimated`（alembic 0008）标记推算时间；estimated 消息只建 manual_review；`quote_detection.strip_quoted_text` 剥引用块 |
| BUG-012 | P1 | 时区偏移被丢弃而非换算：`+03:00` 存成 15:00（应 12:00 UTC）；影响 2h 窗口/±3 天匹配/跨通道去重 | 日期解析各处 | 🚀待部署 | 与 P0 批次一起改为正确换算 UTC |
| BUG-013 | P1 | IMAP 匹配兜底吞新回信：同发件人主题完全不同的新邮件被 `candidates[0]` 兜底挂到旧消息上，新回信永不入库 | `services/attachment_sync.py:125` | 🚀待部署 | `031fce7`：主题非空且精确/包含都未命中时返回 None，走 `_ingest_unmatched_email` 未匹配落库；无主题来信保留取最近兜底。新增 4 项回归测试，全套 216 项通过 |
| BUG-014 | P1 | 前端把后端 naive UTC 当本地时间解析，北京时区全站早 8h；ThreadDetail 的 scheduled_at 回填→`toISOString()` 提交，每确认一轮实际发送提前 8h | `frontend/src/views/ThreadDetail.vue:373,442,456` 等 | 🚀待部署 | `dd2a05f`：新增 `frontend/src/utils/time.js`（dayjs 自带 utc 插件，naive UTC→本地，兼容带 Z/偏移量），ThreadDetail/Mailbox/MailboxSettings 全部改走工具函数；后端接收侧已有 aware→UTC 规范化，无需改动；回填→提交为不动点，npm build 通过 |
| BUG-015 | P1 | `/api/kols` 列表泄漏内部字段：snov_raw_data、snov_custom_fields、contact_notes、personal_intro | `api/kol.py:74` list_kols | ❌未修复 | `KolOut` 白名单已存在但只用于单个 KOL 接口（:141）；列表返回裸 ORM |
| BUG-016 | P1 | 国家/赛道筛选用 `ilike('%值%')` 子串匹配，下拉给的是精确枚举值：选 Niger 连带 Nigeria | `api/kol.py:97-99` | ❌未修复 | 两字段已枚举化/归一（742f43a、9f50d75），查询应改精确匹配 |
| BUG-017 | P1 | 附件文件名写入端允许 `& ' + , ! [ ]`，下载端白名单拒绝 → 存得进下不来（"Rate Card & Pricing.pdf"） | 写 `attachment_sync.py:54` vs 读 `api/attachments.py:97,109` | 🟡部分修复 | 下载正则已含中文/空格/括号（中文名可下了）；`& ' + , ! [ ]` 仍不一致 |
| BUG-018 | P2 | 非 ASCII 凭据/token → `hmac.compare_digest` TypeError → 500：DASHBOARD_PASSWORD 设中文则全站 500 永远登不进 | `api/auth.py:22` `api/webhook.py:327` `api/crawler.py:65` | ❌未修复 | 比较前先 `.encode("utf-8")` 即可 |
| BUG-019 | P2 | `GET /api/feishu/audit` 飞书未配置时 500 | `services/feishu_push.py:1085` + `api/feishu.py:20` | ❌未修复 | 已有显式 raise FeishuSyncError，但 API 层无转换仍 500；对照 process_due_tasks 的 disabled 返回 |
| BUG-020 | P2 | `/docs` `/openapi.json` `/redoc` 匿名可访问 | `main.py:89` FastAPI() | ❌未修复 | 生产靠反代只转 /api 挡住，属纵深缺口；建议 docs_url=None 或挂认证 |
| BUG-021 | P2 | 无请求体 POST 可被跨站表单触发（浏览器缓存 Basic 凭据自动带上），`/api/auto-replies/tasks/{id}/send-now` 会真发信 | `api/auto_replies.py:315` 等 | ❌未修复 | CSRF：要求 JSON body / 自定义头 / SameSite 策略 |
| BUG-022 | P2 | `since_days` 传负数 → 判 falsy → None → 等同全量历史同步 | `api/attachments.py:67` | ❌未修复 | 负数应 422，用 `Field(ge=0)` |
| BUG-023 | P2 | 飞书任务认领置 processing 却不推后 next_retry_at，而 processing 又可认领：调度器与 `POST /api/feishu/process` 并发重复处理同一 KOL | `services/feishu_push.py:962-973` | 🟡部分修复 | 已加 `with_for_update(skip_locked=True)`（仅 PG）防同瞬认领；HTTP 阶段（行锁已释放）仍可被再次认领，认领时应推后 next_retry_at |
| BUG-024 | P2 | `payload_hash` 只写不读，「未变更跳过」设计从未接线 | `services/feishu_push.py:920,938` | ❌未修复 | 同步前比对 hash 相同则 skip |
| BUG-025 | P2 | 全量对账把退避中（retry）任务重置为 pending + next_retry_at=now，指数退避被周期性作废 | `services/feishu_push.py:829-832` | ❌未修复 | enqueue 仅保护 conflict；retry 且未到期的任务不应重置 |
| BUG-026 | P2 | `alembic upgrade head` 在全新 SQLite 库失败：迁移 0007 用了 PostgreSQL 的 `now()` | `alembic/versions/20260724_0007_kol_email_fix.py` | 🚀待部署 | 已改 `CURRENT_TIMESTAMP`；仅影响本地新库（生产 PG 早已跑过 0007），随 P0 批次部署 |

## 批次 D：2026-07-27 飞书「新增行缺数据」溯源

来源：会话「飞书表格缺失数据原因」；生产镜像 `deploy-20260727-imap-enrich` 已部署（52 单测过）。

| 编号 | 问题 | 位置 | 状态 | 修复落点 / 备注 |
|---|---|---|---|---|
| BUG-027 | 本地 uvicorn 带生产飞书凭据 + 本地过期库跑对账，把生产完整行覆盖成占位符并震荡 | 本地 `backend/.env` | ✅已部署 | FEISHU_ENABLE=false + 警示注释；规则见记忆 `feishu-sheet-single-writer-rule`；硬隔离见 BUG-033 |
| BUG-028 | IMAP 补录对匹配不到的第三方回信建「裸 KOL」：不用引用链挂回原会话、不触发画像补全（5 行裸档全是已有达人） | `services/attachment_sync.py` `services/kol_enrich.py` | ✅已部署 | In-Reply-To+References 全链定位原会话挂回原 KOL；确属新达人才建档且触发 enrich；补全成功后重推飞书行 |
| BUG-029 | 存量 22 个 KOL 画像被困本地库（本地/生产 id 序列不同），生产推表只有占位符 | 数据 | 📦数据/运营 | 已按邮箱唯一键填空式回灌（source=local_recovered）。**跨库对数据永远用邮箱不用 id** |
| BUG-030 | message 67/105：Irene 同一封信补录期双入库（BUG-004 修复的漏网） | 生产 message 表 | 📦数据/运营 | 未处理。建议按 `(from_email, subject, received_at)` 扫补录时段清重；报价展示已有去重兜底 |
| BUG-031 | `kol.email` 无唯一/查重约束：BMF 双档（979/993）直接后果 | `models/kol.py` 入库各路径 | ❌未修复 | **不能加简单唯一索引**（同经纪公司多达人共用 collab 邮箱是真实场景）；应「入库前查重 + 命中挂 kol_email 别名而非建新档」或部分唯一索引+人工审 |
| BUG-032 | 遗留行运营决策：利物浦大学 976（误采，建议删+拉黑）、匿名 Creator 1018（待人工确认）、13 个 invalid_reply 隔离达人行去留 | 生产数据/飞书表 | 📦数据/运营 | 系统已隔离不再更新它们，等运营口径 |
| BUG-033 | 环境硬隔离缺失：生产表 token 仍在本地 `.env`，只靠注释约束，换机器/新同事即复发 | `backend/.env` `config.py` | ❌未修复 | 建议：本地移除生产 token，或 config 校验「非生产环境拒绝连生产 spreadsheet token」 |
| BUG-034 | 本地/生产库已分叉（id 序列不同、数据互有缺失） | 流程 | 📦数据/运营 | 建议本地库降只读副本（定期从生产 dump 恢复），本地业务数据不再人肉搬运 |

## 批次 E：代码评审与并发/事务修复

来源：会话「ensure_kol_email 异常处理与 session 状态」评审派生的修复卡片及此前修复。

| 编号 | 问题 | 位置 | 状态 | 修复落点 / 备注 |
|---|---|---|---|---|
| BUG-035 | ensure_kol_email 吞 IntegrityError 后外层 session 带着坏事务继续用 | `services/…ensure_kol_email` | ✅已合并 | `1641a5c`：SAVEPOINT（begin_nested）隔离，失败只回滚子事务 |
| BUG-036 | enqueue_message_sync 对借用的外部 session 直接 commit，打穿调用方事务边界 | `services/feishu_push.py:786` | ✅已合并 | `ce53afe`：借用 session 只 flush，commit/rollback/close 归调用方 |
| BUG-037 | 三处唯一约束上的查后插并发竞争：webhook 的 message_id、auto_reply 的 _upsert_task（source_message_id）、feishu 的 enqueue_message_sync（kol_id） | `api/webhook.py` `services/auto_reply.py` `services/feishu_push.py` | 🚀待部署 | `518e8ca`，已经 `0b00d43` 并入 `fix/db-kol-email-drift`；与 BUG-010 同函数的担忧已核对：_upsert_task 中竞争吸收（begin_nested + IntegrityError）与终态守卫共存，test_dedup_races 随整合后全套 212 项测试通过 |
| BUG-038 | KOL 删除接口外键级联 500 | `api/kol.py` 删除路径 | ✅已合并 | `99be094`：级联清理 + 入库字符串截断防线 + 容器启动自动迁移 |
| BUG-039 | int/float 环境变量空串导致启动 crash | `config.py` | ✅已合并 | `15fc57d` |
| BUG-040 | kol_email 数据漂移 + 邮箱规范化口径不统一 | 入库各路径 | ✅已合并 | `f23b273`；相关架构修复：`b34253b`（连接池+session-over-I/O §8）、`717b173`（snov 三阶段拆分） |

> 批次 E 中「✅已合并」指已进 `fix/db-kol-email-drift` 主干；这些改动**生产尚未部署**，
> 将随下一次整体部署上线（与批次 C 的 🚀待部署项同批走）。

## 已证伪（防止重复报告）

| 编号 | 论断 | 结论 |
|---|---|---|
| FALS-01 | 「删除邮箱凭据会因外键约束 500」 | 不成立：`api/mailbox_credentials.py:187` 删除前已将 `scheduled_reply.mailbox_credential_id` 置空（注意与 BUG-038 的 **KOL** 删除级联是两回事，后者是真 BUG 已修） |

## 风险观察（未实证的隐患，触发条件出现时升格为 BUG）

| 编号 | 风险 | 备注 |
|---|---|---|
| RISK-01 | `get_campaign_replies` 未传分页参数，回信量大后 Snov 若分页会丢数据 | 量大后实测 Snov 行为并补齐 |
| RISK-02 | pydantic v2 弃用警告（class-based Config → ConfigDict，4 处） | 升级 pydantic 大版本前处理 |

## 运维挂账（非代码 BUG，勿丢）

- 关闭服务器 `PasswordAuthentication` + 轮换 SSH 密码（密码曾出现在聊天/交接渠道）。
- 轮换 Snov Client ID/Secret（旧版 HANDOFF.md 曾明文，仍在 git 历史）。
- 本地 `backend/.env` 的 DASHBOARD_* 仍是占位符 → 本地调试看板 503，需自设一组。

---

**下一个可用编号：BUG-041**

## 本文档变更日志

| 日期 | 变更 |
|---|---|
| 2026-07-27 | 初版：收拢 7/26 诊断、7/26 安全、7/27 深度测试、7/27 飞书溯源、代码评审共 40 条 + 1 证伪 + 2 风险项；逐条核对工作区代码确认状态 |
| 2026-07-27 | BUG-037 更正：`518e8ca` 已经 `0b00d43` 并入主干（_upsert_task 守卫共存核对无误），状态 🔀待合并 → 🚀待部署 |
| 2026-07-27 | BUG-013（`031fce7`）、BUG-014（`dd2a05f`）并行修复完成，❌未修复 → 🚀待部署；合并 BUG-037 后的组合状态全套 216 项后端测试通过、前端 build 通过 |
