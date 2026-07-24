"""国家名称归一化（KOL 列表筛选用）。

背景：kol.country 字段存在中英文混杂、缩写、别名（"美国"/"United States"、
"英国"/"United Kingdom"/"GB"/"UK"、"澳洲"/"Australia"/"AU" 等），直接下拉会出现
大量重复语义的选项。本模块提供归一映射：把所有别名映射到一个规范中文名，
筛选时用户选"美国"即等价于匹配 ``United States / 美国`` 等全部别名。

设计原则：
- 不改数据库（DATABASE_DEVELOPMENT.md：业务字段变更走 migration，不轻易批量重写）。
- 仅在查询/展示层归一，归一映射可随数据演进补充。
- 未在映射表里的国家原样透传（保底，不丢数据）。
"""
from __future__ import annotations

# 别名 → 规范名（中文，因为主要用户中文运营）。键全部小写比较，值保留展示大小写。
# 同一规范名下的多个别名共享一个 canonical。
_CANONICAL_MAP: dict[str, str] = {
    # 美国
    "united states": "美国",
    "united states of america": "美国",
    "usa": "美国",
    "us": "美国",
    "america": "美国",
    "美国": "美国",
    # 英国
    "united kingdom": "英国",
    "great britain": "英国",
    "britain": "英国",
    "gb": "英国",
    "uk": "英国",
    "英国": "英国",
    # 加拿大
    "canada": "加拿大",
    "ca": "加拿大",
    "加拿大": "加拿大",
    # 澳大利亚
    "australia": "澳大利亚",
    "au": "澳大利亚",
    "aussie": "澳大利亚",
    "澳洲": "澳大利亚",
    "澳大利亚": "澳大利亚",
    # 德国
    "germany": "德国",
    "de": "德国",
    "deutschland": "德国",
    "德国": "德国",
    # 法国
    "france": "法国",
    "fr": "法国",
    "法国": "法国",
    # 挪威
    "norway": "挪威",
    "no": "挪威",
    "挪威": "挪威",
    # 日本/韩国（数据里有"日韩"这种聚合值，归到"日韩"便于筛选）
    "japan": "日韩",
    "日本": "日韩",
    "south korea": "日韩",
    "korea": "日韩",
    "韩国": "日韩",
    "日韩": "日韩",
    # 其他常见欧洲国家
    "spain": "西班牙",
    "sweden": "瑞典",
    "netherlands": "荷兰",
    "belgium": "比利时",
    "finland": "芬兰",
    "serbia": "塞尔维亚",
    "türkiye": "土耳其",
    "turkey": "土耳其",
    "poland": "波兰",
    "italy": "意大利",
    "denmark": "丹麦",
    "austria": "奥地利",
    "bulgaria": "保加利亚",
    "ireland": "爱尔兰",
    # 其他
    "india": "印度",
    "nigeria": "尼日利亚",
    "united arab emirates": "阿联酋",
    "uae": "阿联酋",
    "indonesia": "印度尼西亚",
    "vietnam": "越南",
    "philippines": "菲律宾",
    "uganda": "乌干达",
    "georgia": "格鲁吉亚",
    "pakistan": "巴基斯坦",
    "mexico": "墨西哥",
    "singapore": "新加坡",
    "malaysia": "马来西亚",
    "colombia": "哥伦比亚",
    "china": "中国",
    "中国": "中国",
    "hong kong": "中国香港",
    "香港": "中国香港",
    "taiwan": "中国台湾",
    "台湾": "中国台湾",
    "欧洲": "欧洲",
}

# 明显无效的国家值（脏数据），筛选选项里排除。
_INVALID_VALUES = {"/", "unknown", "unknown ", "latent spaces", ""}


def canonical_country(raw: str | None) -> str | None:
    """把国家别名归一到规范名。未命中映射的原样返回（去首尾空白）。无效值返回 None。

    用于查询/展示层（筛选项归一）。入库请用 :func:`normalize_country_for_storage`。
    """
    if raw is None:
        return None
    key = raw.strip().lower()
    if not key or key in _INVALID_VALUES:
        return None
    return _CANONICAL_MAP.get(key, raw.strip())


# 入库时视为"未知"的占位符（归一为 None，不写进 DB）。
_STORAGE_PLACEHOLDERS = _INVALID_VALUES | {"unknown", "n/a", "na", "none", "null", "-"}


def normalize_country_for_storage(raw: str | None) -> str | None:
    """入库归一入口：所有写入 kol.country 的路径都应经过它。

    - 已知别名（United States/USA/美国 等）→ 规范中文名（"美国"）。
    - 占位符（/、Unknown、N/A、- 等）→ None（未知值用 NULL，规范 §1.4）。
    - 未命中映射但看起来合法的国家名 → 原样保留（不丢数据，待映射表补充）。

    这样 DB 里的 country 字段就是统一的规范名，查询层无需再做别名展开。
    """
    if raw is None:
        return None
    key = raw.strip().lower()
    if not key or key in _STORAGE_PLACEHOLDERS:
        return None
    return _CANONICAL_MAP.get(key, raw.strip())


def aliases_of(canonical: str) -> list[str]:
    """给定一个规范名，返回它在映射表里的全部别名（含规范名自身，原文形式）。

    用于筛选时把用户选的规范名展开成所有别名做 OR 匹配。
    """
    target = canonical.strip().lower()
    aliases: list[str] = []
    seen_lower: set[str] = set()
    for alias, canon in _CANONICAL_MAP.items():
        if canon.strip().lower() == target:
            if alias not in seen_lower:
                aliases.append(alias)
                seen_lower.add(alias)
    # 规范名自身若不在别名表（理论上在），补上
    if canonical.strip() and canonical.strip().lower() not in seen_lower:
        aliases.append(canonical.strip())
    return aliases
