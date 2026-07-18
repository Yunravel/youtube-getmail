# 计划：把爬虫功能并入项目（Python 重写 + 嵌入后端 + Web UI 触发）

## 已确认的决策（来自用户问答）

| 维度 | 决策 |
|---|---|
| 代码位置 | **Python 重写到 `backend/services/crawler/`**（与后端同栈，可被 API/调度器直接调用） |
| 触发方式 | **Web UI 触发**（BackgroundTasks 后台任务 + 进度轮询，复刻现有 AI 回填模式） |
| 去重粒度 | **(platform, account)**，沿用 `kol_candidate` 现有 UNIQUE 约束 |
| 首期范围 | **发现 + 多平台扩展 + 公开邮箱采集（MX 校验）→ 入发信池** |
| 抓取方式 | **httpx + 可插拔 Fetcher 接口**（首期 httpx，为将来 Playwright/代理留口子） |
| 任务状态 | **内存态**（复刻 `_backfill_jobs` 模式，单进程单并发，重启丢失靠幂等重跑） |

## 关键现状（已探查确认）

1. **Node 脚本仍在 `D:/mail/scripts/`**（`kol_collect.mjs` 622 行 + `kol_enrich_socials.mjs` 266 行 + `kol_probe.mjs` 98 行，7/16 还在跑），作为 Python 重写的**业务规则参考来源**（不是直接集成对象）。
2. **Node 脚本只产 JSON**，JSON→xlsx 是手工环节 —— Python 重写后**直接写库**，消除 JSON→xlsx→push 整条断链。
3. **数据模型已就绪**：`kol_candidate` 表已为爬虫预留列（`crawl_priority`/`email_crawler`/`collect_status`/`collected_at` 等），无需改表、无需迁移。
4. **后台任务模式已成熟**：`api/threads.py` 的 `_backfill_jobs` + `BackgroundTasks` + 进度回调 + 单并发 409 + 状态轮询，新采集任务**完全复刻**这套。
5. **导入漏斗可复用**：`scripts/import_kol_candidate.py:run_import()` 已实现"双写 candidate + 晋升 kol/kol_email + 增量回填空字段"，采集器产出 Python dict 列表后直接调它入库，**不重写入库逻辑**。
6. **Node 业务规则高价值资产**（重写时必须平移）：
   - `productTerms` 字典（4 产品 × 几十个垂类关键词）
   - `allowedByProduct` 产品-国家白名单（如 Dola 只允许 UK）
   - `countryAliases` 国家推断正则 + confidence
   - `businessSignal` 商务邮箱识别（邮箱周围 100 字符内 business/sponsor/collab）
   - `positiveSeeds` / `excludedHandles` / `badEmailFragments` 种子与黑名单
   - 并发池（discovery 3 / channel 6 / MX 30）
   - MX 校验（`dns.resolveMx`）

## 新增配置项（`backend/config.py` + `.env.example`）

```python
# 采集器
CRAWLER_ENABLED: bool = True
CRAWLER_MAX_CONCURRENCY_DISCOVERY: int = 3      # 关键词发现并发
CRAWLER_MAX_CONCURRENCY_CHANNEL: int = 6        # 频道 about 页抓取并发
CRAWLER_MAX_CONCURRENCY_MX: int = 30            # MX 校验并发
CRAWLER_REQUEST_TIMEOUT: int = 20               # 单页超时（秒）
CRAWLER_REQUEST_INTERVAL: float = 0.2           # 请求间隔（秒，礼貌限速）
CRAWLER_USER_AGENT: str = "Mozilla/5.0 ... Chrome/138 Safari/537.36"
CRAWLER_OUTPUT_DIR: str = "./logs/crawler"      # JSON 产物留档目录（可选）
```
**不引入 YouTube API key**（首期纯 HTML 抓取，与 Node 一致）。Fetcher 接口为将来代理/Playwright 预留，但首期不增加这些配置位。

## 新增依赖（`backend/requirements.txt`）

```
# 已有 httpx（无新增）；MX 校验用标准库 dns 不现实，改用 dnspython
dnspython>=2.6.0
```
**只加 1 个依赖**（`dnspython`，对应 Node 的 `dns/promises`）。httpx、openpyxl 已存在。

---

## 实现拆解（7 个工作包）

### 包 1：采集器服务层骨架 `backend/services/crawler/`

新建包结构：
```
backend/services/crawler/
├── __init__.py          # 导出 run_crawl()
├── config_rules.py      # 业务规则常量（从 Node productTerms/allowedByProduct/countryAliases/businessSignal 平移）
├── fetcher.py           # 可插拔 Fetcher 接口 + HttpxFetcher 实现
├── youtube.py           # YouTube 搜索 + about 页解析（对应 kol_collect.mjs 核心）
├── enrich.py            # 多平台外链扩展（对应 kol_enrich_socials.mjs）
├── email_extract.py     # about 页邮箱抽取 + 商务信号识别 + MX 校验
├── normalize.py         # 字段规范化（platform_normalize / parse_int K,M / country 推断）
└── pipeline.py          # run_crawl() 编排：发现→扩展→邮箱→MX→产出 CandidateRow 列表
```

**`fetcher.py`** —— 可插拔接口（关键抽象）：
```python
class Fetcher(Protocol):
    async def fetch_text(self, url: str) -> str: ...
    async def resolve_mx(self, domain: str) -> bool: ...

class HttpxFetcher:
    # 首期实现：httpx.AsyncClient + 配置的 UA/超时/间隔/并发池
```
将来要加 Playwright 或代理时，只需新增 `PlaywrightFetcher` / `ProxiedFetcher`，业务代码不动。

**`pipeline.py:run_crawl(...)`** —— 统一入口（CLI 与 HTTP 共用，复刻 `run_import`/`run_backfill` 模式）：
```python
def run_crawl(
    products: list[str],           # ["Dreamina", "Pippit", ...]
    *,
    enable_enrich: bool = True,    # 多平台扩展
    enable_email: bool = True,     # 公开邮箱采集 + MX
    on_progress=None,              # 回调(done, total, phase)
    commit: bool = False,          # 是否写库
    db: Session = None,
    batch: str = None,
) -> dict:
    """
    返回 stats: {discovered, enriched, with_email, mx_valid,
                 candidate_inserted, candidate_skipped, kol_inserted, ...}
    """
```
内部流程：
1. **发现**：对每个 product × keyword 组合，YouTube 搜索 → 解析频道 handle（`youtube.py`，并发 = `CRAWLER_MAX_CONCURRENCY_DISCOVERY`）
2. **扩展**（可选）：对每个 YouTube 频道抓 about → 抽 IG/TikTok/X 外链（`enrich.py`，并发 = `CRAWLER_MAX_CONCURRENCY_CHANNEL`）
3. **邮箱**（可选）：对每个频道的 about 页抽公开邮箱 + 商务信号识别 + MX 校验（`email_extract.py`，并发 = `CRAWLER_MAX_CONCURRENCY_MX`）
4. **产出**：把结果组装成 `kol_candidate` 行 dict（字段名对齐 ORM，**不是 Excel 中文列名**——因为直接写库不经 Excel）
5. **入库**（`commit=True`）：**直接调 `scripts.import_kol_candidate` 的入库函数**，或抽取其双写逻辑为可复用函数（见包 6）

**关键复用**：`normalize.py` 的 `parse_int`/`parse_emails`/`platform_normalize` **从 `import_email_collection.py` / `import_kol_candidate.py` 抽取共享**，不重复实现。

### 包 2：MX 校验 `email_extract.py`

用 `dnspython`：
```python
import dns.resolver
def mx_valid(domain: str, timeout: float = 3.0) -> bool:
    try:
        dns.resolver.resolve(domain, "MX", lifetime=timeout)
        return True
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.exception.Timeout, Exception):
        return False
```
并发用 `concurrent.futures.ThreadPoolExecutor`（dns 是同步阻塞，包在线程池里）。

### 包 3：API 端点 `backend/api/crawler.py`

新建路由模块，**完全复刻 `threads.py` 的回填任务模式**：

```python
router = APIRouter()

# 内存态任务表（同 _backfill_jobs 模式，重启丢失靠幂等重跑）
_crawl_jobs: dict[str, dict] = {}

class CrawlIn(BaseModel):
    products: list[str]              # ["Dreamina", "Pippit", "Dola", "Kimi"] 子集
    enable_enrich: bool = True
    enable_email: bool = True

@router.post("")
def start_crawl(body: CrawlIn, background_tasks: BackgroundTasks):
    """触发采集后台任务，立即返回 job_id，前端轮询 /crawler/status。"""
    running = [j for j in _crawl_jobs.values() if j["status"] == "running"]
    if running:
        raise HTTPException(409, f"已有采集任务在跑: {running[0]['job_id']}")
    job_id = str(uuid.uuid4())[:8]
    _crawl_jobs[job_id] = {"job_id": job_id, "status": "running",
                           "phase": "discovery", "processed": 0, "total": 0, ...}
    background_tasks.add_task(_run_crawl_job, job_id, body.products, ...)
    return {"job_id": job_id}

@router.get("/status")
def crawl_status():
    """查最新采集任务进度（复刻 /threads/backfill-status）。"""
    if not _crawl_jobs:
        return {"job_id": None, "status": "idle"}
    return list(_crawl_jobs.values())[-1]

@router.get("/products")
def list_products():
    """返回可选产品列表 + 关键词数（供前端 UI 渲染复选框）。"""
    from services.crawler.config_rules import productTerms
    return [{"product": p, "keyword_count": len(kws)} for p, kws in productTerms.items()]
```

**`_run_crawl_job`** 模式同 `_run_backfill_job`：try/except 更新内存状态，`on_progress` 回调更新 phase/processed/total，异常进 `job["error"]`。

**注册**：在 `backend/api/__init__.py` 加 `api_router.include_router(crawler.router, prefix="/crawler", tags=["采集"])`。

### 包 4：入库衔接（关键复用决策）

采集器产出的 `CandidateRow` dict 列表怎么进 `kol_candidate` + `kol` + `kol_email`？

**方案：抽取 `import_kol_candidate.py` 的双写核心为可复用函数**（不破坏现有 Excel 入口）：
- 现状：`run_import(content: bytes, ...)` 接收 xlsx bytes → 内部 `load_rows()` 解析 Excel → `_upsert_candidates(rows, db, batch)` 双写。
- 改造：把 `_upsert_candidates(rows: list[dict], db, batch)` 提炼为模块级公开函数（dict 字段名 = ORM 字段名），采集器和 Excel 入口都调它。
- 采集器产出 dict 时字段名用 **ORM 字段名**（`platform`/`account`/`profile_url`/`contact_email`/`followers`/...），不是 Excel 中文列名。
- `run_import` 保持签名兼容（现有 `/import-candidate` 端点和 `push_to_kol.py` 不动）。

**这是唯一需要改动现有文件的地方**，且是纯重构（提取函数），现有行为不变。

### 包 5：前端 UI `frontend/src/views/Crawler.vue` + 路由 + 菜单

新页面结构（参考 `HotLeads.vue` 的回填任务模式）：
```
┌─ 采集 KOL ────────────────────────────────────┐
│ 产品选择：☐ Dreamina (40 词)  ☐ Pippit (...)  │
│           ☐ Dola (...)      ☐ Kimi (...)      │
│ 选项：    ☑ 多平台扩展  ☑ 公开邮箱采集+MX校验 │
│           [ 开始采集 ]                         │
├─ 当前任务 ────────────────────────────────────┤
│ 阶段：发现频道 (120/300)  ████░░░░ 40%        │
│ （每 2s 轮询 /api/crawler/status）            │
├─ 最近一次结果 ────────────────────────────────┤
│ 发现 300 → 扩展 5451 → 有邮箱 800 → MX有效 620│
│ 入库：candidate +5200 / kol +610 / kol_email +650│
└───────────────────────────────────────────────┘
```

**改动清单**：
1. 新建 `frontend/src/views/Crawler.vue`
2. `frontend/src/router/index.js` 加 `/crawler` 路由（作为 MainLayout 子路由）
3. `frontend/src/layouts/MainLayout.vue` 菜单加一项（icon 用 `@ant-design/icons-vue` 的 `RadarChartOutlined` 或 `SearchOutlined`）
4. `frontend/src/api/index.js` 加 `crawlerApi`：`{ start(body), status(), products() }`

轮询模式完全照搬 `HotLeads.vue` 的回填状态轮询（`setInterval(loadStatus, 2000)`，任务结束停止轮询）。

### 包 6：共享工具抽取

把 `import_email_collection.py` 和 `import_kol_candidate.py` 里**重复的解析函数**抽取到 `backend/scripts/_parse_utils.py`（或 `backend/services/crawler/normalize.py` 复用）：
- `parse_int`（两版：纯数字 vs 支持 K/M —— 统一为支持 K/M 的版本）
- `parse_emails`（`| , ;` 分隔 + 小写去重保序）
- `platform_normalize`
- `_trunc`（按列宽截断）

抽取后两个导入器改为 import 共享版本，行为不变。采集器也用同一份。

### 包 7：文档更新

1. **`DEVELOPMENT.md`** 加节：
   - 6.x 采集流程（在"关键业务流程"里加一节，描述发现→扩展→邮箱→入库）
   - 13.x Cookbook 加"如何新增一个采集产品 / 采集关键词"
   - 3.6 services 层表加 `services/crawler/`
   - 3.7 scripts 表保持（采集走 services 不走 scripts）
2. **`ARCHITECTURE.md`** 更新：
   - 1.2 系统边界图：采集端从"外部世界"移入"本系统"（不再是已剥离的外部工具）
   - 3.3 模块职责矩阵加采集器服务
   - 6.1 数据入库漏斗：新增"本系统内爬虫"分支（与 Excel 上传并列）
   - 11.1 演进脉络：加"采集回归期（第 4 阶段）"
   - 11.2 技术债表：移除"采集端外部"相关条目
3. **`README.md`**：技术栈表加 `dnspython`；功能列表加"KOL 采集（关键词发现+多平台扩展+公开邮箱）"
4. **`.env.example` / `.env.prod.example`**：加 `CRAWLER_*` 配置项

---

## 不做的事（明确边界）

- ❌ **不改 `kol_candidate` 表结构**（已为爬虫预留列，零迁移）
- ❌ **不集成 Node 脚本**（Python 重写，Node 仅作业务规则参考）
- ❌ **首期不做官网深度邮箱采集**（Python 期 `email_finder.py` 的 Playwright 外链官网爬取，留待 Fetcher 接口的 Playwright 实现就绪后）
- ❌ **首期不做定时调度**（仅 Web UI 触发；APScheduler 定时采集作为后续增强，Fetchr 反爬稳定后再加）
- ❌ **不做跨平台身份合并**（去重保持 `(platform, account)`，IG/TikTok/X 各自独立行）
- ❌ **不给采集端点加鉴权**（与看板一致公开访问，沿用现状）
- ❌ **不暴露历史重采 CLI**（首期聚焦 Web UI；CLI 入口 `run_crawl` 已可调，但暂不写专门脚本）

---

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| YouTube 改版导致正则失效 | Fetcher 接口可插拔；正则集中在一处（`youtube.py`）；保留 `kol_probe.mjs` 式冒烟测试思路 |
| IP 被封 | 首期礼貌限速（`CRAWLER_REQUEST_INTERVAL`）；Fetcher 接口预留代理位；并发数可配 |
| 长任务阻塞后台 | 单并发 409（同回填）；任务幂等可重跑；进度可见 |
| 入库重构破坏现有导入 | `_upsert_candidates` 提取是纯重构，现有 `run_import` 签名与行为不变，加测试覆盖 |
| MX 校验拖慢 | 线程池并发 30；MX 失败的行仍入候选库（只是不晋升发信池） |

---

## 待办执行顺序

1. **包 6** 共享工具抽取（先做，后续都依赖）—— 改 `import_*.py` + 新建 `_parse_utils.py`
2. **包 4** 入库衔接 —— 从 `import_kol_candidate.py` 提取 `_upsert_candidates`
3. **包 1+2** 采集器服务层 + MX 校验 —— 核心，从 Node 脚本平移业务规则
4. **包 3** API 端点 —— 复刻回填模式
5. **包 5** 前端 UI —— 复刻回填 UI 模式
6. **包 7** 文档更新
7. 自测：本地起后端，`POST /api/crawler` 跑一次小规模采集（1 个产品 2 个关键词），验证全链路

## 交付物清单

**新建文件**：
- `backend/services/crawler/__init__.py`
- `backend/services/crawler/config_rules.py`
- `backend/services/crawler/fetcher.py`
- `backend/services/crawler/youtube.py`
- `backend/services/crawler/enrich.py`
- `backend/services/crawler/email_extract.py`
- `backend/services/crawler/normalize.py`
- `backend/services/crawler/pipeline.py`
- `backend/api/crawler.py`
- `backend/scripts/_parse_utils.py`
- `frontend/src/views/Crawler.vue`

**修改文件**：
- `backend/requirements.txt`（+ `dnspython>=2.6.0`）
- `backend/config.py`（+ `CRAWLER_*` 配置）
- `backend/api/__init__.py`（+ 注册 crawler 路由）
- `backend/scripts/import_kol_candidate.py`（提取 `_upsert_candidates`，改 import 共享工具）
- `backend/scripts/import_email_collection.py`（改 import 共享工具）
- `backend/.env.example` + `.env.prod.example`（+ `CRAWLER_*`）
- `frontend/src/router/index.js`（+ `/crawler` 路由）
- `frontend/src/layouts/MainLayout.vue`（+ 菜单项）
- `frontend/src/api/index.js`（+ `crawlerApi`）
- `DEVELOPMENT.md` / `ARCHITECTURE.md` / `README.md`（文档更新）