# YouTube 公开博主信息采集工具

这是一个支持 **YouTube Data API v3 官方接口** 和 **公开页面浏览器爬虫** 两种方式的桌面采集工具。它按关键词搜索公开视频，获取视频与频道公开数据，按国家/地区和订阅数筛选，并在采集过程中逐条写入 CSV。

## 已实现

- 多关键词（用 `|` 分隔）和多国家/地区筛选，国家支持中文、英文或两位代码
- 订阅数上下限、每个关键词页数控制
- 批量读取频道与视频数据，频道按“关键词 + 频道 ID”去重
- 23 列 CSV，UTF-8 BOM 编码，可直接用 Excel 打开
- 从频道公开简介识别邮箱、Telegram、WhatsApp、X/Twitter、Facebook、Instagram、TikTok
- 邮箱优先：简介没有邮箱时，可限量检查频道主动公开的官网及 Contact/About 页面
- 可选择“只保存有邮箱或需人工验证的频道”，邮箱写入“联系详情”列
- CSV 增加“邮箱状态”：已获取、需人工验证、已获取且另有需验证邮箱、未发现
- 后台线程、实时日志、停止按钮、逐条落盘
- API Key 仅保存在本机 `config.json`，不会进入日志和 CSV
- 可切换浏览器爬虫模式，无需 API Key，自动使用本机 Chrome 或 Edge

## 运行

### Windows 免安装版

直接双击 [`dist/YouTubeCollector.exe`](dist/YouTubeCollector.exe)。`config.json`、`logs` 和 `output` 会建立在 EXE 所在目录。API 模式需要填写 Key，爬虫模式不需要。

完整的界面操作、邮箱状态说明和故障排查请阅读 [`使用说明.md`](使用说明.md)。

### 两种采集方式

- **官方 API**：速度快、结构稳定，需要 YouTube Data API v3 Key，并消耗项目配额。
- **浏览器爬虫**：无需 Key，读取未登录状态下的 YouTube 公开搜索页与频道公开资料；速度较慢，且 YouTube 页面改版后可能需要更新选择器。本机需安装 Chrome 或 Edge。

爬虫模式中“1 页”按最多 50 个搜索结果计算。程序会依次打开频道公开资料页，因此第一次建议使用 1 个关键词、1 页进行验证。勾选“显示浏览器窗口”可以观察执行过程。

邮箱查找顺序为：频道公开简介 → 频道公开外链 → 公开官网首页 → 同站 Contact/About 等页面。每个网站最多检查 3 页，跳过社交平台、非 HTML、大文件和内网地址。检测到“查看电子邮件地址”、登录要求或 reCAPTCHA 时，不会绕过验证，而是在“邮箱状态”列标记“需人工验证”，人工可从该行的频道链接进入处理。

### 从源码运行

1. 安装 Python 3.10 或更高版本。
2. 在 Google Cloud 项目中启用 YouTube Data API v3，并创建 API Key。
3. 在本目录执行：

```powershell
python -m pip install -r requirements.txt
python main.py
```

首次打开后，把 API Key 填入界面。国家/地区可填写 `美国|英国`、`United States|United Kingdom` 或 `US|GB`；不限制就留空。名称由 YouTube 官方地区接口转换为两位代码。粉丝数上限填 `0` 表示不限；页数填 `-1` 表示一直请求到没有下一页。

## 数据与合规边界

API 模式只请求 YouTube 官方 API 返回的公开字段；爬虫模式只读取无需登录即可看到的搜索页、频道简介和频道公开外链。程序不会登录账号、绕过验证码，也不会抓取未公开邮箱。频道未公开国家或订阅数时，相应字段会留空或记为 0。

`search.list` 是主要配额消耗点。大量关键词或将页数设为 `-1` 前，请先检查 Google Cloud 项目的当日配额。批量列表接口每次最多处理 50 个 ID，本程序已按此限制分批。

## 测试

```powershell
python -m unittest discover -s tests -v
```

## 打包为 Windows EXE（可选）

```powershell
python -m pip install pyinstaller
pyinstaller --noconfirm --onefile --windowed --name YouTubeCollector main.py
```

生成文件为 `dist/YouTubeCollector.exe`。API Key 和 CSV 不会被打进 EXE。
