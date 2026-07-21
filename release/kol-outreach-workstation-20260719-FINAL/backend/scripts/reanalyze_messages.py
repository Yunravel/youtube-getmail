"""Re-run model analysis for existing inbound messages.

This operator-only CLI is intentionally not an HTTP endpoint because each
processed message can create a paid provider request.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

from db import SessionLocal
from models.message import Message
from services.ai_intent import analyze_intent, intent_to_thread_status


def _analyze(item: dict) -> tuple[int, dict]:
    result = analyze_intent(
        email_body=item["body"],
        kol_name=item["kol_name"],
        subject=item["subject"],
    )
    return item["message_id"], result or {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Reanalyze existing inbound email")
    parser.add_argument("--limit", type=int, default=0, help="0 means all matching messages")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--force", action="store_true", help="also replace existing model results")
    args = parser.parse_args()

    db = SessionLocal()
    messages = (
        db.query(Message)
        .filter(Message.direction == "inbound")
        .order_by(Message.received_at.asc(), Message.id.asc())
        .all()
    )
    items = []
    for message in messages:
        analysis = message.ai_analysis or {}
        if not args.force and analysis.get("analysis_source") == "model":
            continue
        items.append({
            "message_id": message.id,
            "body": message.body_text or "",
            "subject": message.subject,
            "kol_name": message.thread.kol.name if message.thread and message.thread.kol else None,
        })
        if args.limit and len(items) >= args.limit:
            break
    db.close()

    if not items:
        print("No messages require model reanalysis.")
        return 0

    results: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = [executor.submit(_analyze, item) for item in items]
        for index, future in enumerate(as_completed(futures), start=1):
            message_id, analysis = future.result()
            if analysis.get("analysis_source") == "model":
                results[message_id] = analysis
            source = "model" if message_id in results else "fallback"
            print(f"Analyzed {index}/{len(items)}: {source}")

    db = SessionLocal()
    saved = 0
    try:
        for message_id, analysis in results.items():
            message = db.query(Message).filter(Message.id == message_id).first()
            if not message:
                continue
            message.ai_analysis = analysis
            thread = message.thread
            if thread:
                thread.last_intent = analysis.get("intent")
                thread.intent_score = analysis.get("intent_score", 0)
                thread.ai_summary = analysis.get("summary")
                next_status = intent_to_thread_status(analysis)
                if next_status:
                    thread.status = next_status
            saved += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    print(f"Saved {saved}/{len(items)} model analyses; fallbacks were not persisted.")
    return 0 if saved == len(items) else 1


if __name__ == "__main__":
    raise SystemExit(main())
