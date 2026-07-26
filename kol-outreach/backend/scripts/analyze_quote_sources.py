"""Analyze local attachments and trusted public rate-card links with the AI API."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from db import SessionLocal
from models import Message
from services.feishu_push import enqueue_messages_sync
from services.quote_source_analysis import (
    analyze_quote_sources,
    collect_quote_sources,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--message-ids", required=True, help="Comma-separated IDs")
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Call the configured AI API and persist structured quote fields",
    )
    args = parser.parse_args()
    message_ids = [
        int(value.strip())
        for value in args.message_ids.split(",")
        if value.strip()
    ]

    db = SessionLocal()
    try:
        messages = [
            message
            for message_id in message_ids
            if (message := db.get(Message, message_id)) is not None
        ]
        payloads = [
            {
                "message_id": message.id,
                "source_count": len(
                    (collected := collect_quote_sources(message))["sources"]
                ),
                "source_types": sorted(
                    {
                        source["type"]
                        for source in collected["sources"]
                    }
                ),
                "errors": collected["errors"],
            }
            for message in messages
        ]
        print(json.dumps({"preview": payloads}, ensure_ascii=False, indent=2))
        if not args.commit:
            return 0

        saved_ids: list[int] = []
        results = []
        for message in messages:
            outcome = analyze_quote_sources(message)
            results.append(
                {
                    "message_id": message.id,
                    "status": outcome["status"],
                    "source_count": outcome.get("source_count", 0),
                    "errors": outcome.get("errors", []),
                }
            )
            commercial = outcome.get("commercial")
            if not commercial or outcome["status"] != "analyzed":
                continue
            message.ai_analysis = {
                **(message.ai_analysis or {}),
                **commercial,
            }
            saved_ids.append(message.id)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    if saved_ids:
        enqueue_messages_sync(saved_ids)
    print(
        json.dumps(
            {"results": results, "saved_message_ids": saved_ids},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
