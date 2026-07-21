"""手动触发一次 IMAP 附件同步（命令行版，不等定时轮询）。

用法：
  python -m scripts.run_attachment_sync                 # 默认：只未读邮件
  python -m scripts.run_attachment_sync --since 30      # 最近 30 天所有邮件
  python -m scripts.run_attachment_sync --since 0       # 全部历史
"""
import argparse
import time

from services.attachment_sync import sync_all_mailboxes


def main() -> int:
    parser = argparse.ArgumentParser(description="手动触发 IMAP 附件同步")
    parser.add_argument(
        "--since", type=int, default=None,
        help="时间范围（天）。不传=只未读；0=全部历史；30=最近30天",
    )
    args = parser.parse_args()

    # --since 不传 → 只未读（轮询模式）；传了 → 手动模式按天数
    if args.since is None:
        mode, since = "unread", None
        print("模式: 只同步未读邮件（轻量）")
    else:
        mode, since = "manual", (args.since if args.since > 0 else None)
        print(f"模式: 手动同步，范围={'全部历史' if since is None else f'最近 {since} 天'}")

    start = time.time()
    result = sync_all_mailboxes(
        mode=mode,
        since_days=since,
        on_mailbox_progress=lambda done, total, email: print(
            f"  [{done}/{total}] {email}", flush=True
        ),
    )
    elapsed = time.time() - start

    print(f"\n=== 完成（用时 {elapsed:.0f}s）===")
    print(f"  邮箱: 成功 {result['mailboxes_ok']}/{result['mailboxes_total']}，失败 {result['mailboxes_failed']}")
    print(f"  抓取邮件 {result['fetched']}，匹配会话 {result['matched']}，下载附件 {result['attached']}")

    # 打印有问题的邮箱（失败/错误）
    problems = [d for d in result.get("details", []) if d.get("errors") or d.get("error_message")]
    if problems:
        print(f"\n=== 有问题的邮箱（{len(problems)} 个）===")
        for d in problems:
            err = d.get("error_message") or f"errors={d.get('errors')}"
            print(f"  {d['email']}: {err}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
