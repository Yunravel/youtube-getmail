"""一键把中台的 webhook 接收端点注册到 Snov（幂等，可重复执行）。

背景：中台的 webhook 接收代码一直是现成的，但 Snov 侧从未登记过订阅，
所以"已发送邮件"事件完全进不来、回信只能靠 2 分钟轮询兜底。本脚本在
中台拥有公网 HTTPS 地址后执行一次即可接通。

用法（在 backend 目录下）：

    # 方式一：命令行传入公网地址
    python -m scripts.subscribe_snov_webhooks --base-url https://kol.example.com

    # 方式二：环境变量（.env / .env.prod 里配 PUBLIC_BASE_URL）
    python -m scripts.subscribe_snov_webhooks

    # 只查看当前 Snov 侧已有的订阅，不做任何修改
    python -m scripts.subscribe_snov_webhooks --list

    # 预演：显示将要注册什么，但不真正调用注册接口
    python -m scripts.subscribe_snov_webhooks --base-url https://xxx --dry-run

没有公网地址时，可用内网穿透临时模拟（见 docs/WEBHOOK_SETUP.md）：
    cloudflared tunnel --url http://localhost:8000
拿到 https://xxxx.trycloudflare.com 后作为 --base-url 传入即可。
注意 trycloudflare 临时域名每次重启会变，正式环境请用固定域名。
"""
import argparse
import os
import sys

# 本机代理会拦截对 api.snov.io 的请求（HANDOFF 已知问题）；
# SnovClient 内部已 trust_env=False，这里再兜一层环境变量。
os.environ.setdefault("NO_PROXY", "*")

from config import settings  # noqa: E402
from services.snov_client import get_snov_client  # noqa: E402

# 中台 webhook.py 支持的三类事件（与 api/snov.py 的 CreateWebhookIn 校验一致）。
EVENTS: list[tuple[str, str]] = [
    ("campaign_email", "sent"),                 # 已发送邮件 → outbound 消息
    ("campaign_reply", "received"),             # 普通回信 → inbound + AI 分析
    ("campaign_reply", "autoreply_received"),   # 自动回复 → inbound（AI 标 auto/ooo）
]


def _hook_endpoint(hook: dict) -> str:
    """Snov 返回字段名不稳定：兼容 end_point / endpoint_url。"""
    return str(hook.get("end_point") or hook.get("endpoint_url") or "")


def main() -> int:
    parser = argparse.ArgumentParser(description="注册 Snov webhook 订阅（幂等）")
    parser.add_argument(
        "--base-url",
        default=os.getenv("PUBLIC_BASE_URL", ""),
        help="中台的公网地址，如 https://kol.example.com（也可用 PUBLIC_BASE_URL 环境变量）",
    )
    parser.add_argument("--list", action="store_true", help="只列出现有订阅，不做修改")
    parser.add_argument("--dry-run", action="store_true", help="预演，不真正注册")
    args = parser.parse_args()

    if not settings.SNOV_CLIENT_ID or not settings.SNOV_CLIENT_SECRET:
        print("[失败] SNOV_CLIENT_ID / SNOV_CLIENT_SECRET 未配置，无法调用 Snov API")
        return 1

    client = get_snov_client()
    try:
        existing = client.list_webhooks()
    except Exception as exc:
        print(f"[失败] 无法读取 Snov 现有订阅: {exc}")
        print("       如在本机执行，请确认代理未拦截 api.snov.io（NO_PROXY=*）。")
        return 1

    print(f"Snov 侧现有订阅 {len(existing)} 条:")
    for hook in existing:
        print(
            f"  - {hook.get('event_object')}/{hook.get('event_action')}"
            f" → {_hook_endpoint(hook)}"
        )
    if args.list:
        return 0

    base_url = (args.base_url or "").strip().rstrip("/")
    if not base_url:
        print("[失败] 缺少公网地址：用 --base-url 传入，或配置 PUBLIC_BASE_URL")
        print("       还没有公网地址？见 docs/WEBHOOK_SETUP.md 的内网穿透方案。")
        return 1
    if not base_url.startswith("https://"):
        print(f"[警告] {base_url} 不是 HTTPS。Snov 要求可公网访问的 HTTPS 地址，")
        print("       http 地址通常无法接收推送，仅建议内网调试用。")
    if not settings.snov_webhook_is_configured:
        print("[失败] SNOV_WEBHOOK_TOKEN 未配置或仍是占位符。")
        print("       中台会拒绝无 token 的 webhook；请先在 .env/.env.prod 配置强随机值。")
        return 1

    endpoint = f"{base_url}/api/webhook/snov?token={settings.SNOV_WEBHOOK_TOKEN}"
    print(f"目标端点: {base_url}/api/webhook/snov?token=<已隐藏>")

    created = skipped = failed = 0
    for event_object, event_action in EVENTS:
        already = any(
            hook.get("event_object") == event_object
            and hook.get("event_action") == event_action
            and _hook_endpoint(hook) == endpoint
            for hook in existing
        )
        label = f"{event_object}/{event_action}"
        if already:
            print(f"  [跳过] {label} 已订阅")
            skipped += 1
            continue
        if args.dry_run:
            print(f"  [预演] 将注册 {label}")
            continue
        try:
            client.create_webhook(event_object, event_action, endpoint)
            print(f"  [成功] 已注册 {label}")
            created += 1
        except Exception as exc:
            print(f"  [失败] {label}: {exc}")
            failed += 1

    if args.dry_run:
        print("预演结束，未做任何修改。")
        return 0
    print(f"完成: 新注册 {created}，已存在 {skipped}，失败 {failed}")
    if created:
        print("验证方法：在 Snov 里给自己发一封测试回信，或等下一封真实回信；")
        print("中台日志应出现 webhook 请求（api.webhook），消息 message_id 不再是 snov:history: 前缀。")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
