"""contact_notes 解析与回填逻辑。

被 alembic migration revision 的 upgrade() 调用，也可独立 import 做 dry-run：

    from migrations.data_migration import parse_contact_notes, dry_run
    dry_run()  # 扫描全库打印解析结果，不写库

设计要点（基于生产库 78 行实测，2026-07-17）：
- contact_notes 是 ``"标签: 值\\n"`` 多行文本，由 import_kol_xlsx.py 的 make_notes() 生成。
- 首行 ``[Dola UK KOL Expansion ...]`` 是 SOURCE_MARKER（项目批次标记）。
- **不能按行 split**：``内容证据`` 字段含整篇社媒帖子（最长 ~4900 字符，内含大量
  换行、``[hashtag 段]``、emoji），按行切会把证据碎片误判成后续标签。
- 正确做法：用"已知标签锚点"定位。每个标签的值 = 该标签起始到下一个已知标签起始之间的文本。
- ``内容证据`` 按设计不拆（用户决定），其值整体忽略。

输出字段映射（见 PIPPIT/DATA_CONSTRAINTS 文档）：
- kol 主表新列：avg_views_10d / language / email_status / email_source /
                collect_status / collect_at / source_link
- project_assessment 表：project_code / fit_status / core_scenario /
                         recommend_angle / kol_category
- kol_email 表：备用邮箱（主 email 由调用方单独写）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.engine import Connection

# ---------------------------------------------------------------------------
# 标签定义
#
# notes 里的标签分两类：
#   1) 输出标签：值映射到某个字段（下面 value 非空的项）。
#   2) 分隔标签：仅作为锚点切割文本，值不输出（value=None）。
#
# ``内容证据`` 是分隔标签——其值是整篇社媒帖子原文（最长 ~4900 字符，含大量
# 换行/emoji/``[hashtag]``），用户决定不结构化。但它**必须**作为锚点存在，否则
# 上一个输出标签（推荐内容角度）的值会一路吞掉证据整段，直到再下一个锚点。
# ---------------------------------------------------------------------------
# (notes 中的标签文本, 输出字段名 或 None 表示仅作分隔锚点)
_LABELS: list[tuple[str, Optional[str]]] = [
    ("达人画像", "kol_category"),
    ("适配 Dola", "fit_status"),          # 值如 "✓"
    ("适配 Pippit", "fit_status"),         # Pippit 批次未来会用到
    ("Dola 核心场景", "core_scenario"),    # 值如 "工作场景【P1】"
    ("推荐内容角度", "recommend_angle"),
    ("内容证据", None),                    # 分隔锚点：值是社媒帖子，不输出
    ("语言", "language"),
    ("10天平均浏览量", "avg_views_10d"),
    ("邮箱状态", "email_status"),
    ("邮箱来源", "email_source"),
    ("采集状态", "collect_status"),
    ("采集时间", "collect_at"),
    ("来源链接", "source_link"),
    ("备用邮箱", "alt_emails_raw"),        # 值如 "a@x.com, b@y.com"
]

# 已知标签名集合（用于锚点定位）。用 ``re.escape`` + 行首锚定。
# 标签行形如 "达人画像: 值" 或 "达人画像:值"。冒号前后可能有空格。
# 带命名 capture ``label`` 以便一次匹配拿到标签名。
_LABEL_PATTERN = re.compile(
    r"^(?P<label>" + "|".join(re.escape(name) for name, _ in _LABELS) + r")\s*[:：]\s*",
    re.MULTILINE,
)


@dataclass
class ParsedNotes:
    """解析结果。未出现的字段不设置，保持 None。"""

    fields: dict[str, Any] = field(default_factory=dict)
    alt_emails_raw: str = ""
    project_code: Optional[str] = None  # 从 SOURCE_MARKER 推断
    warnings: list[str] = field(default_factory=list)


def _infer_project_code(notes: str) -> Optional[str]:
    """从首行 SOURCE_MARKER 推断 project_code。

    ``[Dola UK KOL Expansion ...]`` -> ``dola_uk``
    ``[Pippit ...]``                -> ``pippit_2026``
    无法识别 -> None（调用方可记 warning）。
    """
    first_line = notes.lstrip().split("\n", 1)[0]
    if "Dola" in first_line or "dola" in first_line.lower():
        return "dola_uk"
    if "Pippit" in first_line or "pippit" in first_line.lower():
        return "pippit_2026"
    return None


def parse_contact_notes(notes: str) -> ParsedNotes:
    """状态机解析 contact_notes。

    策略：用 ``_LABEL_PATTERN`` 找到所有已知标签锚点。每个锚点给出
    ``(label_start, value_start, label_name)``——其中 value_start = match.end()。
    每个标签的值 = ``notes[value_start : 下一个锚点的 label_start]``（trim）。

    这样 ``内容证据`` 里的大量换行和 ``[hashtag 段]`` 不会被误判——它们不匹配
    已知标签名，因此内容证据的整段文本被"吸收"进 evidence 标签的值，而我们
    刻意不把 evidence 列入 _LABELS，于是它被忽略。
    """
    result = ParsedNotes()
    if not notes or not notes.strip():
        return result

    result.project_code = _infer_project_code(notes)
    if result.project_code is None:
        result.warnings.append("SOURCE_MARKER 无法识别项目，project_code 置空")

    label_names = dict(_LABELS)
    # 锚点列表：(label_start, value_start, output_name)
    # 注意：out_name 为 None 的标签（如"内容证据"）也要加入，作为分隔锚点；
    # 否则上一个输出标签的值会越过它吞掉一大段。仅输出阶段跳过 None。
    anchors: list[tuple[int, int, Optional[str]]] = []
    for m in _LABEL_PATTERN.finditer(notes):
        out_name = label_names.get(m.group("label"))
        anchors.append((m.start(), m.end(), out_name))

    anchors.sort(key=lambda x: x[0])
    for i, (_lbl_start, val_start, out_name) in enumerate(anchors):
        # 分隔锚点（None）不输出，但它的存在已让上一个标签的 val_end 正确截止。
        if out_name is None:
            continue
        # 值的结束 = 下一个锚点的 label_start；最后一个锚点的值延伸到串尾。
        val_end = anchors[i + 1][0] if i + 1 < len(anchors) else len(notes)
        value = notes[val_start:val_end].strip()
        if not value:
            continue
        if out_name == "alt_emails_raw":
            result.alt_emails_raw = value
        else:
            result.fields[out_name] = value

    return result


# ---------------------------------------------------------------------------
# 值后处理：把原始字符串转成目标列类型
# ---------------------------------------------------------------------------

_TRUE_MARKS = {"✓", "√", "yes", "true", "1", "y"}


def _to_fit_status(raw: str) -> Optional[str]:
    """``适配 Dola: ✓`` -> ``fit``；空/未知 -> None（不臆断 not_fit）。"""
    if raw.strip() in _TRUE_MARKS:
        return "fit"
    return None


def _to_avg_views(raw: str) -> Optional[int]:
    raw = raw.strip().replace(",", "")
    if not raw:
        return None
    try:
        v = int(float(raw))
        return v if v >= 0 else None
    except ValueError:
        return None


_ALT_EMAIL_SPLIT = re.compile(r"[,;|，；]+\s*")


def _split_emails(raw: str) -> list[str]:
    """``"a@x.com, b@y.com"`` -> ``["a@x.com", "b@y.com"]``，去空去重保序。"""
    if not raw:
        return []
    seen: list[str] = []
    for part in _ALT_EMAIL_SPLIT.split(raw):
        e = part.strip().strip(".").lower()
        if e and "@" in e and e not in seen:
            seen.append(e)
    return seen


_EMAIL_NORM = re.compile(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", re.I)


def _normalize_email_loose(raw: str) -> Optional[str]:
    """备用邮箱原文可能有杂乱外围文本，宽松提取第一个合法邮箱。"""
    m = _EMAIL_NORM.search(raw)
    return m.group(0).lower() if m else None


_LANG_MAP = {
    "英语": "en", "英文": "en", "english": "en",
    "中文": "zh", "汉语": "zh", "chinese": "zh",
    "日语": "ja", "japanese": "ja",
    "韩语": "ko", "korean": "ko",
    "法语": "fr", "french": "fr",
    "德语": "de", "german": "de",
    "西班牙语": "es", "spanish": "es",
}


def _to_lang(raw: str) -> Optional[str]:
    key = raw.strip().lower()
    if key in _LANG_MAP:
        return _LANG_MAP[key]
    # 已经是 BCP47 短码
    if re.fullmatch(r"[a-z]{2}(-[a-z0-9]{2,4})?", key):
        return key
    return None


def _parse_collect_at(raw: str) -> Optional[datetime]:
    """采集时间可能是 ``2026-07-15 19:44:16`` 或 Excel 序列残留。非法返回 None。"""
    raw = raw.strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    # 纯数字（Excel 序列残留）——不静默转换，返回 None 并让调用方记 warning
    return None


# ---------------------------------------------------------------------------
# 回填主入口
# ---------------------------------------------------------------------------

@dataclass
class BackfillReport:
    total: int = 0
    updated_kol: int = 0
    inserted_assessments: int = 0
    inserted_emails: int = 0
    skipped_no_notes: int = 0
    skipped_empty_parse: int = 0
    warnings: list[tuple[int, str]] = field(default_factory=list)  # (kol_id, msg)

    def __str__(self) -> str:
        return (
            f"BackfillReport(total={self.total}, updated_kol={self.updated_kol}, "
            f"inserted_assessments={self.inserted_assessments}, "
            f"inserted_emails={self.inserted_emails}, "
            f"skipped_no_notes={self.skipped_no_notes}, "
            f"skipped_empty_parse={self.skipped_empty_parse}, "
            f"warnings={len(self.warnings)})"
        )


def upgrade_backfill(connection: Connection) -> BackfillReport:
    """读取 kol.contact_notes，回填新列 + project_assessment + kol_email。

    幂等：project_assessment/kol_email 用 ON CONFLICT/INSERT OR IGNORE；
    kol 新列用 UPDATE，重复跑只是覆盖相同值。
    """
    report = BackfillReport()
    rows = connection.execute(
        text("SELECT id, email, contact_notes FROM kol "
             "WHERE contact_notes IS NOT NULL AND contact_notes <> ''")
    ).fetchall()
    report.total = len(rows)

    for row in rows:
        kol_id = row[0]
        primary_email = (row[1] or "").strip().lower()
        notes = row[2]
        parsed = parse_contact_notes(notes)
        f = parsed.fields

        if not f and not parsed.alt_emails_raw:
            report.skipped_empty_parse += 1
            continue

        # 1) UPDATE kol 新列（含冗余便利列 fit_project_code，便于不 join 快速筛选）
        collect_at = _parse_collect_at(f.get("collect_at", ""))
        if f.get("collect_at") and collect_at is None:
            report.warnings.append((kol_id, f"采集时间无法解析: {f['collect_at']!r}"))
        avg_views = _to_avg_views(f.get("avg_views_10d", ""))
        language = _to_lang(f.get("language", ""))
        connection.execute(
            text(
                "UPDATE kol SET "
                "avg_views_10d = :avg_views, "
                "language = :language, "
                "email_status = :email_status, "
                "email_source = :email_source, "
                "collect_status = :collect_status, "
                "collect_at = :collect_at, "
                "source_link = :source_link, "
                "fit_project_code = :fit_project_code "
                "WHERE id = :kol_id"
            ),
            {
                "avg_views": avg_views,
                "language": language,
                "email_status": (f.get("email_status") or None),
                "email_source": (f.get("email_source") or None),
                "collect_status": (f.get("collect_status") or None),
                "collect_at": collect_at,
                "source_link": (f.get("source_link") or None),
                "fit_project_code": parsed.project_code,
                "kol_id": kol_id,
            },
        )
        report.updated_kol += 1

        # 2) project_assessment
        if parsed.project_code:
            fit_status = _to_fit_status(f.get("fit_status", ""))
            connection.execute(
                text(
                    # PostgreSQL: ON CONFLICT；SQLite: INSERT OR IGNORE（dialect 在
                    # migration revision 里按 bind 处理；这里用通用写法，由调用方
                    # 的 dialect 决定。为兼顾两者，先 DELETE 再 INSERT 等价且幂等。）
                    "DELETE FROM project_assessment "
                    "WHERE project_code = :pc AND kol_id = :kid"
                ),
                {"pc": parsed.project_code, "kid": kol_id},
            )
            connection.execute(
                text(
                    "INSERT INTO project_assessment "
                    "(project_code, kol_id, fit_status, core_scenario, "
                    " recommend_angle, kol_category, collected_at) "
                    "VALUES (:pc, :kid, :fit, :scenario, :angle, :category, :collected)"
                ),
                {
                    "pc": parsed.project_code,
                    "kid": kol_id,
                    "fit": fit_status,
                    "scenario": (f.get("core_scenario") or None),
                    "angle": (f.get("recommend_angle") or None),
                    "category": (f.get("kol_category") or None),
                    "collected": collect_at,
                },
            )
            report.inserted_assessments += 1

        # 3) kol_email：主邮箱 + 备用邮箱
        if primary_email:
            _upsert_email(connection, kol_id, primary_email, is_primary=True)
            report.inserted_emails += 1
        for alt in _split_emails(parsed.alt_emails_raw):
            norm = _normalize_email_loose(alt) or alt
            if norm and norm != primary_email:
                _upsert_email(connection, kol_id, norm, is_primary=False)
                report.inserted_emails += 1

    return report


def _upsert_email(connection: Connection, kol_id: int, email: str, is_primary: bool) -> None:
    """幂等写入 kol_email。同 (kol_id, email_normalized) 已存在则跳过。"""
    existing = connection.execute(
        text("SELECT 1 FROM kol_email WHERE kol_id = :kid AND email_normalized = :norm"),
        {"kid": kol_id, "norm": email},
    ).first()
    if existing:
        # 已存在；若是主邮箱且当前行不是主，可提升。这里保持简单：不覆盖。
        return
    connection.execute(
        text(
            "INSERT INTO kol_email (kol_id, email, email_normalized, is_primary, source) "
            "VALUES (:kid, :email, :norm, :primary, :source)"
        ),
        {
            "kid": kol_id,
            "email": email,
            "norm": email,
            "primary": is_primary,
            "source": "contact_notes_backfill",
        },
    )


# ---------------------------------------------------------------------------
# Dry-run：不写库，打印解析结果供人工核对
# ---------------------------------------------------------------------------

def dry_run() -> None:
    """独立运行：扫描全库 contact_notes，打印解析摘要。不写库。"""
    from db import engine

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, name, email, contact_notes FROM kol "
                 "WHERE contact_notes IS NOT NULL AND contact_notes <> '' "
                 "ORDER BY id")
        ).fetchall()

    print(f"扫描 {len(rows)} 行有 contact_notes 的 KOL")
    print("=" * 70)
    fit_count = 0
    alt_email_count = 0
    for r in rows:
        kol_id, name, email, notes = r
        parsed = parse_contact_notes(notes)
        f = parsed.fields
        if _to_fit_status(f.get("fit_status", "")):
            fit_count += 1
        alts = _split_emails(parsed.alt_emails_raw)
        if alts:
            alt_email_count += 1
        print(f"id={kol_id} {name[:24]:24s} project={parsed.project_code}")
        print(f"   达人画像={f.get('kol_category')!r} 适配={_to_fit_status(f.get('fit_status',''))!r}")
        print(f"   核心场景={f.get('core_scenario')!r}")
        print(f"   语言={_to_lang(f.get('language',''))!r} 浏览量={_to_avg_views(f.get('avg_views_10d',''))!r}")
        print(f"   邮箱状态={f.get('email_status')!r} 来源={f.get('email_source')!r}")
        print(f"   备用邮箱={alts}")
        if parsed.warnings:
            print(f"   ⚠ {parsed.warnings}")
    print("=" * 70)
    print(f"汇总: fit={fit_count}, 有备用邮箱={alt_email_count}, 总计={len(rows)}")


if __name__ == "__main__":
    dry_run()
