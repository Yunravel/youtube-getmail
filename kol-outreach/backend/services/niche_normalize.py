"""KOL 赛道（niche）归一化与产品名剥离。

背景：``kol.niche`` 历史上混入了三类内容：
  1. **产品名**（Dreamina/Dola/Pippit/Kimi/Hypic/SCRL，占 ~80%）——这其实是"适合推广
     哪个产品"，属于项目评估维度，权威位置是 ``fit_project_code`` / ``project_assessment``。
  2. **内容类目**（生活方式/健身/旅游/教育…，中英文混杂）——这才是真正的赛道。
  3. **脏值**（自定义、/、空）——无意义。

本模块把赛道定义为 **18 个固定枚举**（中文规范名），并提供：
- :func:`classify_niche`：入库归一入口，返回 ``(赛道规范名, 产品名)``。
  产品名单独返回，供调用方写入 ``fit_project_code``；赛道写 ``niche``。
- :func:`normalize_niche_for_storage`：只归一赛道（产品名识别后丢弃，调用方需自行处理）。

设计原则（同 country_normalize）：
- 不改数据库表结构，复用现有 ``niche`` 字段。
- 仅在写入层归一，枚举表可随业务演进补充。
- 未命中映射但看起来合法的类目原样保留（不丢数据）。
"""
from __future__ import annotations

# ===========================================================================
# 产品名识别（这些不该出现在 niche 里，应剥离到 fit_project_code）
# ===========================================================================
# 与 config.py CRAWLER_SCHEDULE_PRODUCTS + config_rules.py 的产品词表保持一致。
# 全部小写比较。值用产品官方拼写（首字母大写），供写 fit_project_code。
_PRODUCT_NAMES: dict[str, str] = {
    "dreamina": "Dreamina",
    "dola": "Dola",
    "pippit": "Pippit",
    "kimi": "Kimi",
    "hypic": "Hypic",
    "scrl": "SCRL",
}

# ===========================================================================
# 18 个固定赛道枚举（中文规范名）
# ===========================================================================
# 键：别名（英文小写 / 中文 / 常见变体），值：规范赛道名。
# 合并来源：crawler profile_parsers._NICHE_KEYWORDS（17 英文类）+ 历史 niche 中文值。
_NICHE_CANONICAL_MAP: dict[str, str] = {
    # 科技/AI（合并 tech + ai + ai 工具类）
    "tech": "科技/AI",
    "ai": "科技/AI",
    "technology": "科技/AI",
    "ai 工具": "科技/AI",
    "ai工具": "科技/AI",
    "ai 工具 / 视频生成 / 创作者效率": "科技/AI",
    "ai image and video generation": "科技/AI",
    "ai image and video generation, creator tools, and practical ai workflows": "科技/AI",
    "ai-focused youtube creator": "科技/AI",
    "ai content creator": "科技/AI",
    "tech & data": "科技/AI",
    "ui/ux designer, creator, tech professional": "科技/AI",
    "ai 工具评测、教程、深度解析、实际用例": "科技/AI",
    "ai 工具 / 内容创作": "科技/AI",
    "ai 影像/电影制作教学": "科技/AI",
    "ai 创作教程(malayalam)": "科技/AI",
    "ai 工具与更新": "科技/AI",
    "ai工具与商业软件评测、产品教程": "科技/AI",
    "ai 工具深度评测与工作流展示": "科技/AI",
    "ui/ux设计、创意工作流、科技工具": "艺术/设计",
    "产品演示": "科技/AI",
    # 游戏
    "gaming": "游戏",
    "gamer": "游戏",
    # 美妆
    "beauty": "美妆",
    "makeup": "美妆",
    "美妆": "美妆",
    # 时尚
    "fashion": "时尚",
    # 健身
    "fitness": "健身",
    "健身": "健身",
    "健身健康": "健身",
    # 美食
    "food": "美食",
    # 旅游（合并多个中文变体）
    "travel": "旅游",
    "旅游攻略": "旅游",
    "旅游出行": "旅游",
    "旅行": "旅游",
    "旅行达人": "旅游",
    "travel & hotel creator": "旅游",
    # 教育（合并学习/研究类）
    "education": "教育",
    "研究生学习": "教育",
    "论文研究": "教育",
    "学生生产力": "教育",
    # 搞笑
    "comedy": "搞笑",
    # 生活方式（合并多个中文生活类）
    "lifestyle": "生活方式",
    "lifestyle creator": "生活方式",
    "生活方式": "生活方式",
    "生活技巧": "生活方式",
    "白领生活": "生活方式",
    "情侣生活": "生活方式",
    # 音乐
    "music": "音乐",
    # 商业财经（合并 business + 金融）
    "business": "商业财经",
    "商业财经": "商业财经",
    "金融理财": "商业财经",
    "个人理财": "商业财经",
    "finance": "商业财经",
    # 育儿
    "parenting": "育儿",
    # 健康
    "health": "健康",
    "健康生活": "健康",
    "wellness": "健康",
    "心理成长": "健康",
    # 艺术设计
    "art": "艺术/设计",
    "艺术设计": "艺术/设计",
    # 汽车
    "auto": "汽车",
    # 体育
    "sports": "体育",
    # UGC创作 / 个人创作者
    "ugc创作": "UGC创作",
    "ugc 创作": "UGC创作",
    "ugc": "UGC创作",
    "ugc/孕期/理疗": "UGC创作",
    "个人创作者/工作室": "UGC创作",
    "自媒体运营": "UGC创作",
    # 人际关系（归到生活方式较勉强，单列会太碎；这里归生活方式）
    "人际关系": "生活方式",
    # 职场/职业/自由职业（归商业财经）
    "职场效率": "商业财经",
    "职业发展": "商业财经",
    "职业/财务/挪威生活": "商业财经",
    "自由职业": "商业财经",
    "副业": "商业财经",
    "远程办公": "商业财经",
    # 复合类目（取首要维度）
    "生活方式/美妆/旅行": "生活方式",
    "生活方式/旅行": "生活方式",
    # 大学生活/学习（归教育）
    "大学生活": "教育",
    "大学学习": "教育",
    # 健康饮食/冥想（归健康）
    "健康饮食": "健康",
    "健康/冥想/女性身心": "健康",
}

# 入库时视为无意义的占位符（归一为 None）。
_NICHE_PLACEHOLDERS = {"", "/", "-", "自定义", "custom", "unknown", "n/a", "na", "none", "null"}

# 规范赛道枚举（供前端/校验引用，与 _NICHE_CANONICAL_MAP 的值集合一致）。
NICHE_ENUM = sorted(set(_NICHE_CANONICAL_MAP.values()))


def _is_product(raw_lower: str) -> str | None:
    """若 raw 是产品名，返回其官方拼写（如 'dreamina' → 'Dreamina'）；否则 None。"""
    return _PRODUCT_NAMES.get(raw_lower)


def classify_niche(raw: str | None) -> tuple[str | None, str | None]:
    """入库归一入口：返回 ``(赛道规范名, 产品名)``。

    - 产品名（Dreamina/Dola…）→ ``(None, "Dreamina")``，供调用方写 fit_project_code。
    - 已知类目别名 → ``(规范赛道, None)``。
    - 占位符（自定义、/）→ ``(None, None)``。
    - 未命中但合法的类目 → ``(原值清洗, None)``（保底，不丢数据）。

    调用方典型用法::

        niche, product = classify_niche(row["niche"])
        kol = Kol(niche=niche, ...)
        if product and not kol.fit_project_code:
            kol.fit_project_code = product
    """
    if raw is None:
        return None, None
    text = raw.strip()
    key = text.lower()
    if not key or key in _NICHE_PLACEHOLDERS:
        return None, None
    # 先识别产品名
    product = _is_product(key)
    if product:
        return None, product
    # 再归一类目
    canon = _NICHE_CANONICAL_MAP.get(key)
    if canon:
        return canon, None
    # 保底：原样保留（去首尾空白），不丢数据
    return text, None


def normalize_niche_for_storage(raw: str | None) -> str | None:
    """只归一赛道部分（产品名识别后丢弃）。

    适用于只关心赛道、不处理产品归属的场景。若需同时拿到产品名，用 classify_niche。
    """
    niche, _ = classify_niche(raw)
    return niche
