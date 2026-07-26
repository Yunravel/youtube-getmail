"""Read-only audit of automatic replies and missing commercial quotes."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from db import SessionLocal
from models import Message, Thread
from services.feishu_push import PLACEHOLDERS, _build_record, _is_invalid_reply


def main() -> None:
    db = SessionLocal()
    try:
        inbound = (
            db.query(Message)
            .filter(Message.direction == "inbound")
            .order_by(Message.received_at.desc(), Message.id.desc())
            .all()
        )
        message_intents = Counter(
            str((message.ai_analysis or {}).get("intent") or "missing")
            for message in inbound
        )

        latest_by_kol: dict[int, Message] = {}
        source = (
            db.query(Message, Thread.kol_id)
            .join(Thread, Message.thread_id == Thread.id)
            .filter(Message.direction == "inbound")
            .order_by(
                Thread.kol_id,
                Message.received_at.desc(),
                Message.id.desc(),
            )
            .all()
        )
        for message, kol_id in source:
            if not _is_invalid_reply(message):
                latest_by_kol.setdefault(kol_id, message)

        missing_quote_intents = Counter()
        no_quote_rows = []
        for kol_id, message in latest_by_kol.items():
            record = _build_record(message)
            if record["完整报价"] != PLACEHOLDERS["commercial"]:
                continue
            intent = str(
                (message.ai_analysis or {}).get("intent")
                or message.thread.last_intent
                or "missing"
            )
            missing_quote_intents[intent] += 1
            analysis = message.ai_analysis or {}
            no_quote_rows.append(
                {
                    "kol_id": kol_id,
                    "message_id": message.id,
                    "intent": intent,
                    "summary": analysis.get("summary"),
                    "collaboration_type": analysis.get("collaboration_type"),
                    "budget_mentioned": analysis.get("budget_mentioned"),
                    "body_preview": (message.body_text or "")[:240].replace("\n", " "),
                }
            )

        automatic_messages = [
            {
                "message_id": message.id,
                "kol_id": message.thread.kol_id,
                "intent": (message.ai_analysis or {}).get("intent"),
                "has_quote": any(
                    (message.ai_analysis or {}).get(key)
                    for key in (
                        "complete_quote",
                        "platform_rate",
                        "external_rate",
                        "budget_mentioned",
                        "quote",
                    )
                ),
                "body_preview": (message.body_text or "")[:240].replace("\n", " "),
            }
            for message in inbound
            if str((message.ai_analysis or {}).get("intent") or "")
            in {"auto_reply", "ooo"}
        ]
        print(
            json.dumps(
                {
                    "inbound_messages": len(inbound),
                    "message_intents": message_intents,
                    "database_kols": len(latest_by_kol),
                    "missing_complete_quote": len(no_quote_rows),
                    "missing_quote_intents": missing_quote_intents,
                    "automatic_messages": automatic_messages,
                    "no_quote_rows": no_quote_rows,
                },
                ensure_ascii=False,
                indent=2,
                default=list,
            )
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
