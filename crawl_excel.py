from __future__ import annotations

import argparse
import logging
import threading
from pathlib import Path

from youtube_collector.social_crawler import SocialProfileCrawler


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从 KOL Excel 名单采集公开联系邮箱")
    parser.add_argument("input", type=Path, help="输入 .xlsx 文件")
    parser.add_argument("-o", "--output", type=Path, help="输出 .xlsx 文件")
    parser.add_argument("--sheet", default="KOL List", help="工作表名称")
    parser.add_argument("--platform", action="append", help="仅处理指定平台，可重复传入")
    parser.add_argument("--start-row", type=int, default=2, help="起始 Excel 行号")
    parser.add_argument("--end-row", type=int, help="结束 Excel 行号（包含）")
    parser.add_argument("--limit", type=int, help="最多处理多少条，适合冒烟测试")
    parser.add_argument("--show-browser", action="store_true", help="显示浏览器窗口")
    parser.add_argument("--no-websites", action="store_true", help="不继续检查主页公开外链网站")
    parser.add_argument("--interval", type=float, default=1.0, help="每个账号之间等待秒数")
    return parser.parse_args()


def main() -> int:
    args = arguments()
    output = args.output or args.input.with_name(args.input.stem + "_邮箱采集结果.xlsx")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    crawler = SocialProfileCrawler(
        logging.getLogger("social-crawler"),
        status=print,
        show_browser=args.show_browser,
        interval=max(0, args.interval),
        scan_public_websites=not args.no_websites,
    )
    crawler.crawl_excel(
        args.input,
        output,
        sheet_name=args.sheet,
        platforms=set(args.platform) if args.platform else None,
        start_row=args.start_row,
        end_row=args.end_row,
        limit=args.limit,
        stop_event=threading.Event(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
