"""采集器业务规则常量。

平移自 Node ``kol_collect.mjs`` / ``kol_enrich_socials.mjs`` 的高价值业务资产：
关键词字典、产品-国家白名单、国家推断别名、排除名单、邮箱黑名单、正向种子。

这些规则是采集质量的核心，与具体抓取实现（httpx / 正则）解耦——换抓取方式
不必动这里。新增产品或关键词只改本文件。
"""
from __future__ import annotations

import re

# ===== 采集关键词字典 =====
# 每个产品对应一组内容垂类关键词，用于平台搜索发现候选 KOL
productTerms: dict[str, list[str]] = {
    "Dreamina": [
        "industrial design", "product design", "product visualization",
        "concept design", "3D modeling", "3D rendering", "Blender", "Blender AI",
        "Maya 3D", "Cinema 4D", "C4D motion design", "Houdini VFX",
        "ZBrush sculpting", "KeyShot rendering", "Fusion 360 design",
        "SolidWorks design", "Rhino 3D", "Unreal Engine art", "Unity game art",
        "game CG", "game cinematics", "game environment art",
        "character modeling", "environment modeling",
        "architectural visualization", "3D animation", "VFX artist", "CG artist",
        "digital art", "render challenge", "scientific illustration",
        "scientific visualization", "biomedical illustration",
        "engineering visualization", "text to 3D AI", "image to 3D AI",
        "generative 3D", "AI design tools", "AI image generation",
        "creative AI workflow",
    ],
    "Pippit": [
        "AI filmmaking", "generative filmmaking", "AI short film",
        "cinematic AI video", "AI storyteller", "AI visual storytelling",
        "AI video workflow", "AI video tutorial", "AI film director",
        "commercial director", "filmmaking workflow", "video production tools",
        "Premiere Pro workflow", "Unreal Engine filmmaking",
        "Blender filmmaking", "virtual production", "AI creative workflow",
        "AI tool education", "generative AI education", "AI industry insights",
        "AI trends analysis", "AI case studies", "AI podcast",
        "generative AI podcast", "AI newsletter", "AI content creator",
        "AI advertising creative", "AI commercial filmmaking",
        "screenwriting AI", "AI animation filmmaking",
    ],
    "Kimi": [
        "AI productivity", "office productivity", "AI search", "AI writing",
        "AI workflow", "AI for work", "AI product review", "AI news",
        "technology insights", "AI trends", "knowledge sharing",
        "study productivity", "career development", "educational content",
        "content creation workflow", "digital productivity", "future of work",
        "no code automation", "business automation", "research productivity",
        "note taking productivity", "remote work productivity",
        "creator productivity", "ChatGPT productivity",
    ],
    "Dola": [
        "UK lifestyle creator", "UK healthy lifestyle", "UK fitness lifestyle",
        "UK healthy eating", "UK travel tips", "UK life hacks",
        "UK personal growth", "UK college life", "UK university study",
        "UK study tips", "UK study productivity", "UK academic research tips",
        "UK exam preparation", "UK student productivity",
        "UK work productivity", "UK office productivity", "UK corporate life",
        "UK career development", "UK remote work", "UK freelancing",
        "UK side hustle", "UK ecommerce creator", "UK digital marketing",
        "UK social media manager", "UK entrepreneurship", "UK content creator",
        "UK UGC creator", "UK short form video creator",
        "UK education creator", "UK career creator", "UK productivity creator",
        "UK storytelling creator", "UK product demo creator",
    ],
}

# 关键词查询修饰符（与 Node makeQueries 一致）
# 非 Dola 产品：每个词生成 {term}、{term} {generic}、{region} {term} 三组查询
# Dola 产品：关键词本身已带 UK 前缀，只生成 {term} tips
genericModifiers = ["tutorial", "tips", "workflow", "creator"]
regionModifiers = [
    "USA", "UK", "Canada", "Europe", "Germany", "France", "Netherlands",
    "Spain", "Italy", "Sweden", "Norway", "Denmark", "Finland", "Portugal",
    "Brazil", "Mexico", "Argentina", "Colombia", "Chile", "Japan", "Korea",
]

# ===== 产品-国家白名单 =====
# 候选 KOL 的推断国家必须在产品的允许集合内，才视为该产品合格候选。
# （Unknown 国家视为所有产品都合格，避免误杀。）
allowedByProduct: dict[str, set[str]] = {
    "Dreamina": {
        "United States", "United Kingdom", "Canada", "Netherlands", "Spain",
        "France", "Italy", "Switzerland", "Germany", "Denmark", "Norway",
        "Finland", "Sweden", "Portugal", "Brazil", "Mexico", "Argentina",
        "Colombia", "Chile", "Japan", "South Korea",
    },
    "Pippit": {
        "United States", "United Kingdom", "Canada", "Netherlands", "Spain",
        "France", "Italy", "Switzerland", "Germany", "Denmark", "Norway",
        "Finland", "Sweden", "Portugal", "Brazil", "Mexico", "Argentina",
        "Colombia", "Chile", "Japan", "South Korea",
    },
    "Kimi": {
        "United States", "United Kingdom", "Canada", "Netherlands", "Spain",
        "France", "Italy", "Switzerland", "Germany", "Denmark", "Norway",
        "Finland", "Sweden", "Portugal",
    },
    "Dola": {"United Kingdom"},
}

# ===== 国家推断别名（正则）=====
# 从频道标题+简介里推断国家。顺序敏感：越靠前优先级越高。
# 元组 (国家, 编译正则)
countryAliases: list[tuple[str, re.Pattern]] = [
    ("United Kingdom", re.compile(r"\b(united kingdom|uk|british|england|scotland|wales|london)\b", re.I)),
    ("United States", re.compile(r"\b(united states|usa|u\.s\.|american|new york|los angeles|california)\b", re.I)),
    ("Canada", re.compile(r"\b(canada|canadian|toronto|vancouver)\b", re.I)),
    ("Netherlands", re.compile(r"\b(netherlands|dutch|amsterdam)\b", re.I)),
    ("Spain", re.compile(r"\b(spain|spanish|madrid|barcelona)\b", re.I)),
    ("France", re.compile(r"\b(france|french|paris)\b", re.I)),
    ("Italy", re.compile(r"\b(italy|italian|milan|rome)\b", re.I)),
    ("Switzerland", re.compile(r"\b(switzerland|swiss|zurich)\b", re.I)),
    ("Germany", re.compile(r"\b(germany|german|berlin|munich)\b", re.I)),
    ("Denmark", re.compile(r"\b(denmark|danish|copenhagen)\b", re.I)),
    ("Norway", re.compile(r"\b(norway|norwegian|oslo)\b", re.I)),
    ("Finland", re.compile(r"\b(finland|finnish|helsinki)\b", re.I)),
    ("Sweden", re.compile(r"\b(sweden|swedish|stockholm)\b", re.I)),
    ("Portugal", re.compile(r"\b(portugal|portuguese|lisbon)\b", re.I)),
    ("Brazil", re.compile(r"\b(brazil|brazilian|sao paulo|rio de janeiro)\b", re.I)),
    ("Mexico", re.compile(r"\b(mexico|mexican|mexico city)\b", re.I)),
    ("Argentina", re.compile(r"\b(argentina|argentinian|buenos aires)\b", re.I)),
    ("Colombia", re.compile(r"\b(colombia|colombian|bogota)\b", re.I)),
    ("Chile", re.compile(r"\b(chile|chilean|santiago)\b", re.I)),
    ("Japan", re.compile(r"\b(japan|japanese|tokyo)\b", re.I)),
    ("South Korea", re.compile(r"\b(south korea|korean|seoul)\b", re.I)),
]

# ===== 排除名单 =====
# 已知低质/无关 handle，发现阶段直接跳过（小写，带 /@ 前缀以匹配搜索结果格式）
excludedHandles: set[str] = {
    f"/@{h.lower()}" for h in [
        "anvi__lifestyle", "inteligencianapratica_", "ceylinvibes_",
        "ugc_with_meg", "ana_ai_finds", "scalewithimanshu", "fatihlyfe",
        "growithrobin", "gokceercan", "davidugc31", "nickintech1",
        "kilic_usastyle",
    ]
}

# ===== 邮箱黑名单片段 =====
# 含这些片段的邮箱视为无效（示例域、平台自身、占位符等）
badEmailFragments: list[str] = [
    "example.com", "email.com", "sentry", "wixpress", "youtube.com",
    "google.com", "schema.org", "domain.com", "yourname@",
]

# ===== 商务邮箱识别关键词 =====
# 邮箱周围 100 字符内出现这些词 → 判定为"公开商务邮箱"（而非普通公开邮箱）
businessSignalRe: re.Pattern = re.compile(
    r"\b(business|sponsor|collab|partnership|management|inquir|contact|work with|brand|booking|press)\b",
    re.I,
)

# ===== 正向种子（多平台扩展用）=====
# 来自需求表的人工筛选优质账号，合并进多平台候选池。
# 元组 (平台, handle, 产品, 备注)
positiveSeeds: list[tuple[str, str, str, str]] = [
    ("Instagram", "paultrillo", "Pippit", "需求表正向种子：director"),
    ("X", "PJaccetturo", "Pippit", "需求表正向种子：filmmaker"),
    ("Instagram", "rourke", "Pippit", "需求表正向种子：AI工具教育"),
    ("Instagram", "sebintel", "Pippit", "需求表正向种子：AI工具教育"),
    ("Instagram", "jadheshvp", "Pippit", "需求表正向种子：AI影视/叙事"),
    ("Instagram", "fiqri_fox", "Pippit", "需求表正向种子：AI影视/叙事"),
    ("Instagram", "reels_mon01", "Pippit", "需求表正向种子：AI影视/叙事"),
    ("Instagram", "ryann.ananta", "Pippit", "需求表正向种子：AI影视/叙事"),
    ("Instagram", "theimagehs", "Pippit", "需求表正向种子：AI影视/叙事"),
    ("Instagram", "soegimitro", "Pippit", "需求表正向种子：AI影视/叙事"),
    ("Instagram", "kentdhani", "Pippit", "需求表正向种子：AI影视/叙事"),
    ("YouTube", "sankyverse", "Pippit", "需求表正向种子：AI工具教育"),
    ("YouTube", "kingy-ai", "Pippit", "需求表正向种子：AI工具教育"),
    ("YouTube", "IvanKv", "Pippit", "需求表正向种子：AI工具教育"),
    ("YouTube", "MikkelLassalle", "Pippit", "需求表正向种子：AI工具教育"),
    ("YouTube", "Web-3-World", "Pippit", "需求表正向种子：AI工具教育"),
    ("YouTube", "CryptoBrosVortex", "Pippit", "需求表正向种子：AI工具教育"),
    ("YouTube", "jonlawedu", "Pippit", "需求表正向种子：AI工具教育"),
    ("YouTube", "TaylorCutFilms", "Pippit", "需求表正向种子：AI影视"),
]

# 多平台扩展时，从简介外链抽取社媒账号要排除的路径段（非用户名）
socialHandleExcluded: set[str] = {
    "accounts", "explore", "p", "reel", "reels", "stories", "share",
    "intent", "home", "search", "i", "hashtag",
}


def make_queries(
    products: list[str],
    custom_product_terms: dict[str, list[str]] | None = None,
) -> list[tuple[str, str]]:
    """生成 (产品, 查询词) 列表。与 Node makeQueries 逻辑一致。

    非 Dola 产品：每个词生成 term / term+generic / region+term 三组
    Dola 产品：每个词生成 term tips（词本身已带 UK 前缀）
    """
    entries: list[tuple[str, str]] = []
    custom_product_terms = custom_product_terms or {}
    for product in products:
        terms = productTerms.get(product, custom_product_terms.get(product, []))
        for index, term in enumerate(terms):
            entries.append((product, term))
            if product == "Dola":
                entries.append((product, f"{term} tips"))
            else:
                entries.append((product, f"{term} {genericModifiers[index % len(genericModifiers)]}"))
                entries.append((product, f"{regionModifiers[index % len(regionModifiers)]} {term}"))
    return entries
