"""
GPT 意向分析(核心模块)
回信进来 → GPT 输出结构化 JSON → 更新 thread 的 intent/score/status

这是整套系统的"大脑":把运营从"读每一封回信"解放出来,只看 Hot Lead
"""
import json
import logging
from typing import Optional
from openai import OpenAI

from config import settings

logger = logging.getLogger(__name__)

_client: Optional[OpenAI] = None


def _get_client() -> Optional[OpenAI]:
    global _client
    if _client is None and settings.OPENAI_API_KEY:
        client_options = {"api_key": settings.OPENAI_API_KEY}
        if settings.OPENAI_BASE_URL:
            client_options["base_url"] = settings.OPENAI_BASE_URL
        _client = OpenAI(**client_options)
    return _client


# 意向分析的系统提示
SYSTEM_PROMPT = """你是 KOL 合作意向分析师。分析博主给我们的回信邮件,判断他的合作意向。

输出严格的 JSON,字段如下:
{
  "intent": "high|medium|low|negative|ooo|auto_reply",
  "intent_score": 0到100的整数,
  "budget_mentioned": "邮件里提到的报价/预算金额(字符串),没有就 null",
  "key_questions": ["对方问的关键问题列表,空数组如果没有"],
  "timeline": "urgent|flexible|none(对方提到的时间紧迫度)",
  "summary": "一句话中文总结这封回信的核心意思",
  "suggested_action": "立即跟进|温和跟进|暂不跟进|放弃",
  "collaboration_type": "博主愿意接受的具体交付物类型(字符串),如 'Dedicated Video / Integration / Short / Bundle',从报价表或合作描述里提取;没提到就 null",
  "platform_rate": "博主针对本次发信平台(默认 YouTube)的报价(字符串),如 '$2,000 USD' 或 '$2000 dedicated + $1000 integration';只提取与本平台相关的价格;没提到就 null",
  "external_rate": "博主针对其他平台(TikTok/Instagram/X 等)或跨平台套餐的报价(字符串);只提取非本平台的;没提到就 null",
  "complete_quote": "邮件中全部完整报价，覆盖所有平台、内容形式、套餐、数量阶梯和币种；精炼为一个字符串但不得遗漏价格；没提到就 null",
  "creator_tags": ["2到3个达人内容标签，如 AI工具、效率软件、YouTube教程；只能根据邮件签名、频道信息或正文判断，无法判断时返回空数组"]
}

判定标准:
- high(75-100): 明确表达合作兴趣,问报价/细节/流程,甚至主动约时间
- medium(40-74): 态度积极但模糊,"maybe / could work / tell me more"
- low(10-39): 礼貌但冷淡,"not now / busy / maybe later"
- negative(0-9): 明确拒绝,"not interested / stop emailing"
- ooo: 不在办公室自动回复
- auto_reply: 其他自动回复(如 "I received your email")

报价提取说明:
- collaboration_type: 博主能接的交付物形式,从报价表/合作描述里归纳(如 dedicated video/integration/short/UGC/bundle)
- platform_rate: 本次发信平台(默认 YouTube)的报价。报价表若标了平台归属,取 YouTube 那部分;若只给一个总价且未标平台,视作本平台报价
- external_rate: 明确标注为其他平台(TikTok/IG)的报价,或跨平台套餐总价。本平台专属价格不计入此项
- complete_quote: 汇总邮件里出现的全部报价，不区分本平台/外部平台，保留套餐和数量阶梯
- creator_tags: 返回2到3个简短内容标签，不要把价格、国家或合作意向当标签
- 三者都没提到就填 null,不要编造

只输出 JSON,不要 markdown 代码块,不要解释。"""


def analyze_intent(
    email_body: str,
    kol_name: Optional[str] = None,
    subject: Optional[str] = None,
) -> Optional[dict]:
    """
    分析单封回信的意向

    Args:
        email_body: 回信正文
        kol_name: 博主名(给 GPT 上下文)
        subject: 主题

    Returns:
        分析结果 dict,失败返回 None
        {
            "intent": "high",
            "intent_score": 85,
            "budget_mentioned": null,
            "key_questions": ["What's your budget?"],
            "timeline": "flexible",
            "summary": "对方明确表达合作兴趣并询问预算",
            "suggested_action": "立即跟进",
            "collaboration_type": "Dedicated Video / Integration",
            "platform_rate": "$2,000 USD",
            "external_rate": null
        }
    """
    client = _get_client()
    if not client:
        logger.warning("OPENAI_API_KEY 未配置,走规则兜底")
        return _rule_based_fallback(email_body)

    # 截断超长邮件(GPT-4o-mini 上下文够,但省 token)
    body = (email_body or "")[:3000]

    user_prompt = f"""分析这封 KOL 回信:

主题: {subject or '(无)'}
博主: {kol_name or '(未知)'}
正文:
{body}

输出 JSON。"""

    try:
        resp = client.chat.completions.create(
            model=settings.OPENAI_MODEL_INTENT,  # gpt-4o-mini,省钱
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=1000,
            extra_body=(
                {"thinking": {"type": "disabled"}}
                if settings.OPENAI_BASE_URL and "api.deepseek.com" in settings.OPENAI_BASE_URL
                else {}
            ),
            temperature=0.1,   # 分析任务要确定性,低温度
            response_format={"type": "json_object"},  # 强制 JSON 输出
        )
        result = json.loads(resp.choices[0].message.content)

        # 字段校验 + 默认值
        result.setdefault("intent", "low")
        result.setdefault("intent", "low")
        result.setdefault("intent_score", 0)
        result.setdefault("budget_mentioned", None)
        result.setdefault("key_questions", [])
        result.setdefault("timeline", "none")
        result.setdefault("summary", "")
        result["analysis_source"] = "model"
        result["analysis_model"] = settings.OPENAI_MODEL_INTENT
        result.setdefault("suggested_action", "暂不跟进")
        result.setdefault("collaboration_type", None)
        result.setdefault("platform_rate", None)
        result.setdefault("external_rate", None)
        result.setdefault("complete_quote", None)
        result.setdefault("creator_tags", [])

        logger.info(f"意向分析: intent={result['intent']} score={result['intent_score']}")
        return result

    except json.JSONDecodeError as e:
        logger.error(f"GPT 返回非 JSON: {e}")
        return _rule_based_fallback(email_body)
    except Exception as e:
        logger.error(f"意向分析失败: {e}")
        return _rule_based_fallback(email_body)


# ===== 规则兜底(没 API key 或 GPT 失败时用) =====
HOT_KEYWORDS = [
    "interested", "yes", "sounds good", "let's do it", "send details",
    "pricing", "budget", "rate card", "how much", "quote", "let's talk",
    "schedule a call", "book a call", "let's chat", "send over",
    "i'm in", "count me in", "would love to", "open to",
]
NEGATIVE_KEYWORDS = [
    "not interested", "unsubscribe", "remove me", "stop emailing",
    "no thanks", "do not contact", "take me off",
]
OOO_KEYWORDS = [
    "out of office", "out of the office", "auto-reply", "auto reply",
    "automatic reply", "currently away", "on leave", "vacation",
]


def _rule_based_fallback(email_body: str) -> dict:
    """无 API 时的简单关键词兜底"""
    body = (email_body or "").lower()

    if any(k in body for k in OOO_KEYWORDS):
        return _result("ooo", 10, "不在办公室自动回复", "暂不跟进")
    if any(k in body for k in NEGATIVE_KEYWORDS):
        return _result("negative", 5, "明确拒绝合作", "放弃")
    if any(k in body for k in HOT_KEYWORDS):
        return _result("high", 70, "表达合作兴趣(关键词匹配)", "立即跟进")

    return _result("low", 20, "未检测到明确意向", "温和跟进")


def _result(intent, score, summary, action, **extra):
    r = {
        "intent": intent,
        "intent_score": score,
        "analysis_source": "rules",
        "analysis_model": None,
        "budget_mentioned": None,
        "key_questions": [],
        "timeline": "none",
        "summary": summary,
        "suggested_action": action,
        "collaboration_type": None,
        "platform_rate": None,
        "external_rate": None,
        "complete_quote": None,
        "creator_tags": [],
    }
    r.update(extra)
    return r


# ===== 阈值规则:把 intent/score 映射到 thread 状态 =====
def intent_to_thread_status(analysis: dict) -> str:
    """
    根据分析结果决定 thread 的新状态
    - high → hot(推到看板顶部)
    - medium → warming
    - low → open(等后续跟进)
    - negative → closed
    - ooo/auto → 保持原状态(不提醒运营)
    """
    intent = analysis.get("intent", "low")
    score = analysis.get("intent_score", 0)

    if intent in ("ooo", "auto_reply"):
        return None  # 不改状态
    if intent == "high" or score >= 75:
        return "hot"
    if intent == "medium" or 40 <= score < 75:
        return "warming"
    if intent == "negative":
        return "closed"
    return "open"
