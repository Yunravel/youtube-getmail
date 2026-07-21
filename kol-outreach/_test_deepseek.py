"""临时验证脚本：确认项目能通过现有 config 调通 DeepSeek。
跑通后即可删除此文件。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "backend"))

from config import settings
from openai import OpenAI

print("=" * 50)
print("1. 读取到的配置（key 会脱敏显示）")
print("=" * 50)
print(f"OPENAI_BASE_URL       = {settings.OPENAI_BASE_URL}")
print(f"OPENAI_API_KEY        = {settings.OPENAI_API_KEY[:8]}...{settings.OPENAI_API_KEY[-4:] if settings.OPENAI_API_KEY else '(空)'}")
print(f"OPENAI_MODEL_INTENT   = {settings.OPENAI_MODEL_INTENT}")
print(f"OPENAI_MODEL_PERSONALIZE = {settings.OPENAI_MODEL_PERSONALIZE}")

if not settings.OPENAI_API_KEY or settings.OPENAI_API_KEY == "sk-xxxxx":
    print("\n[FAIL] key 没填或还是占位符。回 Step 2 改 .env.prod。")
    sys.exit(1)

print("\n" + "=" * 50)
print("2. 实际调用 DeepSeek（意向分析测试）")
print("=" * 50)

client = OpenAI(api_key=settings.OPENAI_API_KEY, base_url=settings.OPENAI_BASE_URL)
try:
    resp = client.chat.completions.create(
        model=settings.OPENAI_MODEL_INTENT,
        messages=[
            {"role": "system", "content": "你是 KOL 意向分析助手，只输出 JSON。"},
            {"role": "user", "content": '判断这条回信的意向，只输出 JSON：{"intent":"positive/negative/neutral"}。回信内容："好的，发一份合作资料过来看看。"'},
        ],
        max_tokens=60,
    )
    print(f"[OK] 模型返回：{resp.choices[0].message.content}")
    print(f"     消耗 token：{resp.usage.total_tokens}")
except Exception as e:
    print(f"[FAIL] 调用失败：{type(e).__name__}: {e}")
    sys.exit(1)

print("\n" + "=" * 50)
print("3. 调用成功！DeepSeek 已接入项目。")
print("=" * 50)
print("现在可以删除本脚本（_test_deepseek.py）。")
