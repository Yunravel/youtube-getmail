"""CLI: import all current Snov prospect lists into the configured database."""
import json

from db import SessionLocal, init_db
from services.snov_client import get_snov_client
from services.snov_contacts import sync_snov_contacts


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        result = sync_snov_contacts(db, get_snov_client())
        print(json.dumps(result, ensure_ascii=False))
    finally:
        db.close()


if __name__ == "__main__":
    main()
