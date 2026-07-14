# Excel KOL 名单邮箱采集

本地脚本会读取 Excel 中的 `KOL List` 工作表，处理 `平台`、`账号`、`主页链接`，并在原有数据右侧新增：

- 联系邮箱
- 邮箱状态
- 邮箱来源
- 公开外链
- 采集状态
- 采集时间

支持表中现有的 YouTube、Instagram、TikTok 和 X。只读取无需登录即可看到的公开资料，不登录账号、不绕过验证码。输入文件不会被修改。

## 图形界面使用

运行 `启动工具.cmd` 后，打开 **Excel 名单邮箱采集** 标签页，然后：

1. 选择输入 `.xlsx` 名单；
2. 确认工作表名称（默认 `KOL List`）；
3. 勾选需要处理的平台；
4. 设置输出 `.xlsx` 文件位置；
5. 点击“开始采集”。

窗口中的运行日志会实时显示当前处理的账号；点击“停止”会在当前账号处理完成后保存已完成的结果。

## 安装

```powershell
python -m pip install -r requirements.txt
```

程序会优先调用本机 Chrome，其次调用 Edge。

## 先抽样

```powershell
python crawl_excel.py "KOL名单.xlsx" --platform YouTube --limit 3 --show-browser
python crawl_excel.py "KOL名单.xlsx" --platform Instagram --limit 3
python crawl_excel.py "KOL名单.xlsx" --platform TikTok --limit 3
python crawl_excel.py "KOL名单.xlsx" --platform X --limit 3
```

## 处理完整名单

```powershell
python crawl_excel.py "KOL名单.xlsx" -o "KOL名单_邮箱采集结果.xlsx"
```

默认会继续检查达人在主页中明确公开的官网及其 Contact/About 页面。若只读社交平台主页：

```powershell
python crawl_excel.py "KOL名单.xlsx" --no-websites
```

发生中断时，输出文件中已有结果会保留。可以把该输出文件作为下一次输入，并用 `--start-row` 从指定 Excel 行继续：

```powershell
python crawl_excel.py "KOL名单_邮箱采集结果.xlsx" -o "KOL名单_邮箱采集结果_续跑.xlsx" --start-row 120
```

TikTok 等平台可能针对当前网络或无登录浏览器只返回访问限制页面。程序会在“采集状态”中记录，不会把这种情况当作“确认没有邮箱”。
