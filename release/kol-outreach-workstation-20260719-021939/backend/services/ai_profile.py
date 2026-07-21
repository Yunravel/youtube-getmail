"""KOL 画像与报价提取（独立于意向分析）。

与 ``ai_intent.py`` 解耦：意向分析判断"要不要跟进"，本模块提取"KOL 是谁、
报价多少、合作条件"，供 HotLead 导出和画像回填使用。两者共用同一个 DeepSeek
客户端配置，但 prompt 和输出字段完全独立，互不影响。

典型回信特征（基于实测 38 条 inbound）：
- 平台/handle/主页链接常嵌在正文里（"https://www.youtube.com/@xxx"）。
- 内容定位是 KOL 自述（"My channel focuses on AI image and video generation"）。
- 报价以 Option 1/2/3 + bundle 形式给出，含币种（$ / £ / USD / GBP）。
- 合作条件：付款比例（50% upfront）、授权期限（7-day usage）、档期（July）。

输出 JSON 字段（找不到的为 null，不要编造）：
  platform, social_handle, profile_url, followers_count,
  creator_niche, content_focus, deliverables, payment_terms, usage_rights
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from openai import OpenAI

from config import settings

logger = logging.getLogger(__name__)

_client: Optional[OpenAI] = None


def _get_client() -> Optional[OpenAI]:
    """与 ai_intent.py 相同的客户端初始化（复用同一 DeepSeek 配置）。"""
    global _client
    if _client is None and settings.OPENAI_API_KEY:
        opts = {"api_key": settings.OPENAI_API_KEY}
        if settings.OPENAI_BASE_URL:
            opts["base_url"] = settings.OPENAI_BASE_URL
        _client = OpenAI(**opts)
    return _client


SYSTEM_PROMPT = """你是 KOL 画像与报价提取器。从 KOL 回复合作邀请的邮件正文里，提取结构化信息。

只输出严格 JSON，字段如下（找不到就给 null，不要编造，不要猜）：
{
  "platform": "youtube|instagram|tiktok|x|website|null",
  "social_handle": "博主自报的社交账号，带@，找不到为 null",
  "profile_url": "博主在正文里给出的主页/频道链接，找不到为 null",
  "followers_count": "粉丝数整数（只提取博主自报的，不要估算），找不到为 null",
  "creator_niche": "博主自报的身份/领域，如 'Travel & Hotel Creator' 或 'AI image and video generation'，找不到为 null",
  "content_focus": "内容方向中文一句话概括，如 'AI 工具 / 视频生成 / 创作者效率'，找不到为 null",
  "deliverables": "博主列出的报价套餐明细原文（如 'Dedicated YouTube video $600; sponsored integration $250'），找不到为 null",
  "payment_terms": "付款条件原文（如 '50% upfront + 50% before publication'），找不到为 null",
  "usage_rights": "授权/使用权条款原文（如 '7-day paid usage rights'），找不到为 null"
}

提取规则：
- platform 只能是 youtube/instagram/tiktok/x/website 之一，根据正文里提到的平台或链接 host 判断；多个时取博主主阵地。
- followers_count 必须是博主在正文里明确写出的数字，不能从报价推断，不能估算；没写就是 null。
- deliverables 把所有报价档位和套餐完整保留（Option 1/2/3 + bundle），含币种。
- 三个合作条件字段（payment_terms/usage_rights）严格区分：付款方式、授权期限、档期不要混。
- 正文里没有的信息一律 null，宁可空也不要猜。

只输出 JSON，不要 markdown 代码块，不要解释。"""


def extract_profile(
    email_body: str,
    kol_name: Optional[str] = None,
    subject: Optional[str] = None,
) -> Optional[dict]:
    """从单封回信提取画像与报价信息。

    Returns:
        含 9 个字段的 dict，失败返回 None。成功时附带 analysis_source /
        analysis_model 便于事后审计与增量回填。
    """
    client = _get_client()
    if not client:
        logger.warning("OPENAI_API_KEY 未配置，画像提取跳过")
        return None

    body = (email_body or "")[:3000]
    if not body.strip():
        return None

    user_prompt = (
        f"分析这封 KOL 回信：\n\n"
        f"主题: {subject or '(无)'}\n"
        f"博主: {kol_name or '(未知)'}\n"
        f"正文:\n{body}\n\n输出 JSON。"
    )

    try:
        resp = client.chat.completions.create(
            model=settings.OPENAI_MODEL_INTENT,  # 复用意向分析同一模型，省钱且够用
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=900,
            extra_body=(
                {"thinking": {"type": "disabled"}}
                if settings.OPENAI_BASE_URL and "api.deepseek.com" in settings.OPENAI_BASE_URL
                else {}
            ),
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        result = json.loads(resp.choices[0].message.content)

        # 默认值兜底，保证 9 个字段都存在
        for key in (
            "platform", "social_handle", "profile_url", "followers_count",
            "creator_niche", "content_focus", "deliverables",
            "payment_terms", "usage_rights",
        ):
            result.setdefault(key, None)
        result["analysis_source"] = "model"
        result["analysis_model"] = settings.OPENAI_MODEL_INTENT
        logger.info(
            f"画像提取: platform={result['platform']} handle={result['social_handle']} "
            f"deliverables={'有' if result['deliverables'] else '无'}"
        )
        return result

    except json.JSONDecodeError as e:
        logger.error(f"画像提取返回非 JSON: {e}")
        return None
    except Exception as e:
        logger.error(f"画像提取失败: {e}")
        return None


# ---------------------------------------------------------------------------
# 报价字符串后处理：把 budget_mentioned / deliverables 解析成 (最小金额, 币种)
# ---------------------------------------------------------------------------

import re

_AMOUNT_RE = re.compile(
    # 金额：支持千分位逗号(1,200)、空格(1 200)、或不带(1200)。货币符号在前后均可。
    # 用一个统一的"数字体"模式，避免 1200 被切成 120。
    r"(?:GBP|USD|£|\$)\s*(\d[\d,\s.]*\d|\d)"
    r"|(\d[\d,\s.]*\d|\d)\s*(?:GBP|USD|£|\$)",
    re.IGNORECASE,
)
_CURRENCY_HINT = re.compile(r"(GBP|USD|£|\$)", re.IGNORECASE)


def parse_min_quote(raw: Optional[str]) -> tuple[Optional[int], Optional[str]]:
    """从报价字符串里提取"起步价"（最小金额）和币种。

    输入例（实测样本）：
        "$2,000 for video campaign, $1,700 for carousel post"  -> (1700, "$")
        "$1200-$1400"                                            -> (1200, "$")
        "£1,500"                                                  -> (1500, "£")
        "$2500 for reel, $3000 for both IG and TikTok"           -> (2500, "$")
        "rate card attached"                                      -> (None, None)

    Returns:
        (min_amount_int, currency_symbol)。无法解析返回 (None, None)。
        currency_symbol 为 "$"/"£"/None；"$" 既可能是 USD 也可能是其他，
        导出层负责标注，本函数只保留原始符号。

    注意：金额后处理会过滤掉明显过小的数（<10，通常是序号/百分比的误匹配）。
    """
    if not raw or not raw.strip():
        return None, None

    matches = _AMOUNT_RE.findall(raw)
    amounts: list[int] = []
    for g1, g2 in matches:
        text = (g1 or g2).strip()
        # 去掉千分位逗号和空格
        text = text.replace(",", "").replace(" ", "")
        try:
            val = int(float(text))
        except ValueError:
            continue
        # 过滤明显误匹配：<10 几乎不可能是报价（序号、百分比、版本号）
        if val < 10:
            continue
        amounts.append(val)

    if not amounts:
        return None, None

    cur_match = _CURRENCY_HINT.search(raw)
    currency = cur_match.group(1) if cur_match else None
    if currency:
        currency = currency.upper()
        if currency == "GBP":
            currency = "£"
        elif currency == "USD":
            currency = "$"

    return min(amounts), currency
