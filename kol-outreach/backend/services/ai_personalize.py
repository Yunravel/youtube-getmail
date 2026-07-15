"""
GPT 个性化开场白生成
读取 KOL 最近视频标题 → GPT 分析赛道 + 生成定制开场白 → 写入 kol.personal_intro

这是 cold email 回复率的生死线:开场白必须个性化,模板群发 = 垃圾箱
"""
import logging
from typing import Optional
from openai import OpenAI

from config import settings

logger = logging.getLogger(__name__)

# 全局 client(没有 key 时为 None,降级走 mock)
_client: Optional[OpenAI] = None


def _get_client() -> Optional[OpenAI]:
    global _client
    if _client is None and settings.OPENAI_API_KEY:
        _client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


# 系统提示:让 GPT 扮演"KOL 外联开场白写手"
SYSTEM_PROMPT = """你是一位资深的 KOL 合作外联专家,擅长写高回复率的个性化开场白。

你的任务:
1. 分析博主的频道名、赛道、最近视频标题,理解他的内容风格和受众
2. 写出 1-2 句高度个性化的开场白,让对方感觉"这封信是认真看了我的内容才写的"
3. 语气自然、真诚,像同行之间的交流,不要营销腔

要求:
- 必须具体提到他最近的一条视频或内容主题(用中括号 [视频: xxx] 标注引用来源)
- 50 词以内,英文输出(海外 KOL)
- 不要用 "Dear / I hope this email finds you well" 这类烂大街开头
- 直接输出开场白正文,不要任何解释、前缀"""


def generate_intro(
    kol_name: str,
    niche: Optional[str],
    recent_videos: Optional[list],
    our_product: str = "our product",
) -> Optional[str]:
    """
    为单个 KOL 生成个性化开场白

    Args:
        kol_name: 博主名
        niche: 赛道
        recent_videos: 最近视频标题列表
        our_product: 我们的产品/合作简述(让 GPT 知道在推什么)

    Returns:
        开场白文本,失败返回 None
    """
    client = _get_client()
    if not client:
        logger.warning("OPENAI_API_KEY 未配置,生成 mock 开场白")
        return _mock_intro(kol_name, niche, recent_videos)

    # 组装用户提示
    videos_text = ""
    if recent_videos:
        videos_text = "\n".join(f"- {v}" for v in recent_videos[:10])

    user_prompt = f"""博主信息:
- 名字: {kol_name}
- 赛道: {niche or '未知'}
- 最近视频标题:
{videos_text or '(无数据)'}

我们要推广的产品/合作: {our_product}

请生成个性化开场白(英文,50词以内,必须引用他最近的一条视频)。"""

    try:
        resp = client.chat.completions.create(
            model=settings.OPENAI_MODEL_PERSONALIZE,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.8,   # 稍高温度增加多样性
            max_tokens=150,
        )
        intro = resp.choices[0].message.content.strip()
        logger.info(f"为 {kol_name} 生成开场白: {intro[:50]}...")
        return intro
    except Exception as e:
        logger.error(f"GPT 生成开场白失败({kol_name}): {e}")
        return None


def _mock_intro(kol_name, niche, recent_videos):
    """没配 API key 时的兜底,生成占位文本方便联调"""
    video_hint = ""
    if recent_videos:
        video_hint = f" Loved your recent video [video: {recent_videos[0]}],"
    return (
        f"Hey {kol_name.split()[0] if kol_name else 'there'},"
        f"{video_hint} the execution was next-level."
        f" We've got something that'd resonate with your audience in the {niche or 'creator'} space."
    )


def analyze_niche(recent_videos: Optional[list]) -> Optional[str]:
    """轻量分析:从视频标题推断赛道(给 GPT-4o-mini,省 token)"""
    if not recent_videos:
        return None
    client = _get_client()
    if not client:
        return None
    try:
        videos_text = "\n".join(f"- {v}" for v in recent_videos[:10])
        resp = client.chat.completions.create(
            model=settings.OPENAI_MODEL_INTENT,
            messages=[{
                "role": "user",
                "content": f"根据以下视频标题,用一个英文词概括内容赛道(如 tech/gaming/beauty/fitness/education/comedy):\n{videos_text}\n\n只输出一个词。"
            }],
            temperature=0.2,
            max_tokens=20,
        )
        return resp.choices[0].message.content.strip().lower()
    except Exception as e:
        logger.error(f"赛道分析失败: {e}")
        return None
