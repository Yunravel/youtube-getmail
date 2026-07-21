## 根因总结

`"$10k"` 被识别成 `10` 的根因在 `backend/services/ai_profile.py:142-148`：

```python
_AMOUNT_RE = re.compile(
    r"(?:GBP|USD|£|\$)\s*(\d[\d,\s.]*\d|\d)"   # 字符类 [\d,\s.] 不含 k/m
    r"|(\d[\d,\s.]*\d|\d)\s*(?:GBP|USD|£|\$)",
    re.IGNORECASE,
)
```

正则字符类 `[\d,\s.]` **只接受数字、千分位逗号、空格、小数点**，不含字母单位。匹配 `"$10k"` 时，到 `k` 就停下 → 只抓出 `"10"` → `int(float("10"))=10` 恰好通过 `<10` 过滤器 → 返回 `(10, "$")`。

更糟的是 `$5k`/`$2.5k`/`$1.2m` 抓出 `5`/`2`/`1` 全被 `<10` 过滤掉 → 报价列直接显示"未报价"。

**同源 bug**：`backend/scripts/backfill_kol_profile.py:156` 把 AI 返回的 `followers_count` 转 int 时也用 `int(float(str(value).replace(",", "")))`，LLM 返回 `"1.2k"` 会写入 `followers=1`。

代码库里已有 3 处正确的 k/m 转换实现（`_parse_utils.parse_int`、`crawler/normalize.parse_subscriber_count`、`import_kol_xlsx.parse_count`），但上述两处没复用。

---

## 修复方案

### 改动 1：`backend/services/ai_profile.py:142-199`

**(a) 扩展正则 `_AMOUNT_RE`** —— 字符类加入 `k`/`K`/`m`/`M`，让单位后缀进入捕获组：

```python
_AMOUNT_RE = re.compile(
    # 金额：支持千分位逗号、空格、小数点，以及 k/K(千) m/M(百万) 单位后缀
    r"(?:GBP|USD|£|\$)\s*(\d[\d,\s.]*\d[kKmM]?|\d[kKmM]?)"
    r"|(\d[\d,\s.]*\d[kKmM]?|\d[kKmM]?)\s*(?:GBP|USD|£|\$)",
    re.IGNORECASE,
)
```

> 注意：`\d[kKmM]?` 单独分支覆盖单字符金额带单位（如 `$5k`、`$2m`）。`[\d,\s.]*\d[kKmM]?` 覆盖多位数/带逗号带单位（如 `$1,200k`、`$10k`）。

**(b) 在 `parse_min_quote` 循环里做单位转换**（第 174-185 行附近）：

```python
for g1, g2 in matches:
    text = (g1 or g2).strip()
    text = text.replace(",", "").replace(" ", "")
    # 识别单位后缀 k/K = 千、m/M = 百万（与项目其它 parse_int 行为一致）
    mult = 1
    if text and text[-1] in "kKmM":
        suffix = text[-1].lower()
        mult = 1_000 if suffix == "k" else 1_000_000
        text = text[:-1]
    try:
        val = int(float(text) * mult)
    except ValueError:
        continue
    if val < 10:
        continue
    amounts.append(val)
```

修复后回归样本：
- `"$10k"` → `10000` ✓（核心 bug）
- `"$5k"` → `5000` ✓（之前被 `<10` 过滤丢弃）
- `"$2.5k"` → `2500` ✓
- `"$1.2m"` → `1_200_000` ✓
- `"$1,200"` → `1200` ✓（保持原行为）
- `"rate card attached"` → `(None, None)` ✓

### 改动 2：`backend/scripts/backfill_kol_profile.py:154-160`

复用项目已有的 `_parse_utils.parse_int`（已有 k/m 转换），替换裸 `int(float(...))`：

```python
from scripts._parse_utils import parse_int   # 加到文件顶部 import 区
...
if ai_field == "followers_count":
    value = parse_int(value)   # 复用已有 k/m 转换
    if not value or value <= 0:
        continue
```

修复后：`"1.2k"` → `1200` ✓，`"15,000"` → `15000` ✓（保持兼容）。

### 改动 3：新增回归测试 `backend/tests/test_parse_quote.py`

当前 `backend/tests/` 下**没有**针对 `parse_min_quote` 的测试，所以这个 bug 一直没被捕获。补一个纯函数单测（无需 DB，参考 `test_mailbox.py` 的 unittest 风格）：

```python
import unittest
from services.ai_profile import parse_min_quote

class ParseMinQuoteTest(unittest.TestCase):
    def test_plain_usd(self):
        assert parse_min_quote("$1,200") == (1200, "$")
        assert parse_min_quote("$1200-$1400") == (1200, "$")

    def test_k_suffix(self):
        # 核心 bug 回归
        assert parse_min_quote("$10k") == (10000, "$")
        assert parse_min_quote("$5k") == (5000, "$")
        assert parse_min_quote("$2.5k") == (2500, "$")
        assert parse_min_quote("£3K") == (3000, "£")

    def test_m_suffix(self):
        assert parse_min_quote("$1.2m") == (1_200_000, "$")

    def test_no_quote(self):
        assert parse_min_quote("rate card attached") == (None, None)
        assert parse_min_quote("") == (None, None)

if __name__ == "__main__":
    unittest.main()
```

---

## 验证方式

```bash
cd D:/mail/kol-outreach/backend
python -m pytest tests/test_parse_quote.py -v
# 或无需 pytest：
python -m unittest tests.test_parse_quote -v
```

---

## 影响范围与风险

- **正向影响**：`parse_min_quote` 被 `export_quote.py:89` 消费，修复后 HotLead 导出的 Excel 报价列对 `10k`/`5k`/`2.5k`/`1.2m` 形式的报价将正确显示，不再丢数据。
- **回归风险低**：正则扩展是向后兼容的（原来能匹配的纯数字依然能匹配），只是新增 k/m 后缀处理；`<10` 过滤器保留，不会引入新的误匹配。
- **不触碰** LLM 提取逻辑（`ai_profile.extract_profile`）、`export_quote` SQL、爬虫模块。
- **历史数据**：已导出的 Excel 不会自动修复，需要重新对受影响 thread 触发 `/export`（数据源 `message.ai_analysis.budget_mentioned` 未变，重跑导出即可）。

## 不做的事

- 不重构抽离统一金额解析模块（用户选择保守方案）。
- 不修改 LLM prompt（`budget_mentioned` 字段原本就是字符串原样保存，后处理才是 bug 源头）。
- 不写数据迁移脚本（重新触发导出即可，源数据无需迁移）。