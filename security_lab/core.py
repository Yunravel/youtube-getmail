from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_LIMITS = {
    "account": 20,
    "session": 20,
    "device": 40,
    "ip": 100,
}

MOCK_ACCOUNTS = {
    "lab-token-alice": "alice",
    "lab-token-bob": "bob",
}

MOCK_CHANNELS = {
    "channel-001": "creator1@example.test",
    "channel-002": "creator2@example.test",
    "channel-003": "creator3@example.test",
}


@dataclass(frozen=True)
class LabResponse:
    status: int
    body: dict[str, object]


class EmailSecurityLab:
    """SQLite-backed model of a protected email-view endpoint.

    The database transaction intentionally serializes quota checks and increments.
    This is a local training target, not an integration with any real service.
    """

    def __init__(
        self,
        database: str | Path,
        limits: dict[str, int] | None = None,
        now=time.time,
    ):
        self.database = str(database)
        self.limits = dict(DEFAULT_LIMITS if limits is None else limits)
        self.now = now
        self._schema_lock = threading.Lock()
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 15000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        with self._schema_lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS channels (
                    channel_id TEXT PRIMARY KEY,
                    email TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS usage_counters (
                    dimension TEXT NOT NULL,
                    identity TEXT NOT NULL,
                    usage_day TEXT NOT NULL,
                    count INTEGER NOT NULL CHECK(count >= 0),
                    PRIMARY KEY (dimension, identity, usage_day)
                );
                CREATE TABLE IF NOT EXISTS request_ids (
                    account_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (account_id, request_id)
                );
                CREATE TABLE IF NOT EXISTS captcha_challenges (
                    token_hash TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    ip TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    used INTEGER NOT NULL DEFAULT 0 CHECK(used IN (0, 1))
                );
                CREATE TABLE IF NOT EXISTS captcha_grants (
                    token_hash TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    ip TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    used INTEGER NOT NULL DEFAULT 0 CHECK(used IN (0, 1))
                );
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    account_id TEXT,
                    details_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                """
            )
            connection.executemany(
                "INSERT OR IGNORE INTO channels(channel_id, email) VALUES (?, ?)",
                MOCK_CHANNELS.items(),
            )

    def reset(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                DELETE FROM usage_counters;
                DELETE FROM request_ids;
                DELETE FROM captcha_challenges;
                DELETE FROM captcha_grants;
                DELETE FROM audit_log;
                """
            )

    @staticmethod
    def authenticate(token: str) -> str | None:
        return MOCK_ACCOUNTS.get(token)

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _day(self) -> str:
        return datetime.fromtimestamp(self.now(), tz=timezone.utc).date().isoformat()

    @staticmethod
    def _identities(account_id: str, ip: str, device_id: str, session_id: str) -> dict[str, str]:
        return {
            "account": account_id,
            "ip": ip,
            "device": device_id,
            "session": session_id,
        }

    def _audit(
        self,
        connection: sqlite3.Connection,
        event_type: str,
        account_id: str | None,
        details: dict[str, object],
    ) -> None:
        connection.execute(
            "INSERT INTO audit_log(event_type, account_id, details_json, created_at) VALUES (?, ?, ?, ?)",
            (event_type, account_id, json.dumps(details, sort_keys=True), self.now()),
        )

    def _counts(
        self,
        connection: sqlite3.Connection,
        identities: dict[str, str],
        day: str,
    ) -> dict[str, int]:
        result: dict[str, int] = {}
        for dimension, identity in identities.items():
            row = connection.execute(
                "SELECT count FROM usage_counters WHERE dimension=? AND identity=? AND usage_day=?",
                (dimension, identity, day),
            ).fetchone()
            result[dimension] = int(row["count"]) if row else 0
        return result

    def _issue_challenge(
        self,
        connection: sqlite3.Connection,
        account_id: str,
        ip: str,
        device_id: str,
        session_id: str,
    ) -> str:
        token = secrets.token_urlsafe(32)
        connection.execute(
            """
            INSERT INTO captcha_challenges(
                token_hash, account_id, ip, device_id, session_id, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                self._hash_token(token),
                account_id,
                ip,
                device_id,
                session_id,
                self.now() + 120,
            ),
        )
        return token

    def _consume_grant(
        self,
        connection: sqlite3.Connection,
        grant: str,
        account_id: str,
        ip: str,
        device_id: str,
        session_id: str,
    ) -> tuple[bool, str]:
        row = connection.execute(
            "SELECT * FROM captcha_grants WHERE token_hash=?",
            (self._hash_token(grant),),
        ).fetchone()
        if not row:
            return False, "unknown_grant"
        if row["used"]:
            return False, "replayed_grant"
        if row["expires_at"] < self.now():
            return False, "expired_grant"
        expected = (account_id, ip, device_id, session_id)
        actual = (row["account_id"], row["ip"], row["device_id"], row["session_id"])
        if actual != expected:
            return False, "grant_binding_mismatch"
        updated = connection.execute(
            "UPDATE captcha_grants SET used=1 WHERE token_hash=? AND used=0",
            (self._hash_token(grant),),
        ).rowcount
        return (updated == 1, "ok" if updated == 1 else "replayed_grant")

    def view_email(
        self,
        *,
        auth_token: str,
        channel_id: str,
        ip: str,
        device_id: str,
        session_id: str,
        request_id: str,
        captcha_grant: str = "",
    ) -> LabResponse:
        account_id = self.authenticate(auth_token)
        if not account_id:
            return LabResponse(401, {"error": "invalid_auth_token"})
        if not all((channel_id, ip, device_id, session_id, request_id)):
            return LabResponse(400, {"error": "missing_identity_or_request_id"})

        day = self._day()
        identities = self._identities(account_id, ip, device_id, session_id)
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                if not connection.execute(
                    "SELECT 1 FROM channels WHERE channel_id=?", (channel_id,)
                ).fetchone():
                    connection.rollback()
                    return LabResponse(404, {"error": "channel_not_found"})
                try:
                    connection.execute(
                        "INSERT INTO request_ids(account_id, request_id, created_at) VALUES (?, ?, ?)",
                        (account_id, request_id, self.now()),
                    )
                except sqlite3.IntegrityError:
                    self._audit(connection, "request_replay_blocked", account_id, {"request_id": request_id})
                    connection.commit()
                    return LabResponse(409, {"error": "request_id_replayed"})

                counts = self._counts(connection, identities, day)
                exceeded = [
                    dimension
                    for dimension, count in counts.items()
                    if count >= self.limits[dimension]
                ]
                if exceeded:
                    if captcha_grant:
                        valid, reason = self._consume_grant(
                            connection, captcha_grant, account_id, ip, device_id, session_id
                        )
                        if not valid:
                            self._audit(connection, "grant_rejected", account_id, {"reason": reason})
                            connection.commit()
                            return LabResponse(403, {"error": reason})
                    else:
                        challenge = self._issue_challenge(
                            connection, account_id, ip, device_id, session_id
                        )
                        self._audit(
                            connection,
                            "captcha_required",
                            account_id,
                            {"dimensions": exceeded},
                        )
                        connection.commit()
                        return LabResponse(
                            429,
                            {
                                "error": "captcha_required",
                                "limited_dimensions": exceeded,
                                "challenge_token": challenge,
                            },
                        )

                for dimension, identity in identities.items():
                    connection.execute(
                        """
                        INSERT INTO usage_counters(dimension, identity, usage_day, count)
                        VALUES (?, ?, ?, 1)
                        ON CONFLICT(dimension, identity, usage_day)
                        DO UPDATE SET count=count+1
                        """,
                        (dimension, identity, day),
                    )
                row = connection.execute(
                    "SELECT email FROM channels WHERE channel_id=?", (channel_id,)
                ).fetchone()
                self._audit(connection, "email_viewed", account_id, {"channel_id": channel_id})
                connection.commit()
                return LabResponse(200, {"channel_id": channel_id, "email": row["email"]})
            except Exception:
                connection.rollback()
                raise

    def verify_captcha(
        self,
        *,
        auth_token: str,
        challenge_token: str,
        answer: str,
        ip: str,
        device_id: str,
        session_id: str,
    ) -> LabResponse:
        account_id = self.authenticate(auth_token)
        if not account_id:
            return LabResponse(401, {"error": "invalid_auth_token"})
        if answer != "LAB-OK":
            return LabResponse(403, {"error": "captcha_failed"})

        token_hash = self._hash_token(challenge_token)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM captcha_challenges WHERE token_hash=?", (token_hash,)
            ).fetchone()
            if not row:
                connection.rollback()
                return LabResponse(404, {"error": "challenge_not_found"})
            if row["used"]:
                connection.rollback()
                return LabResponse(409, {"error": "challenge_replayed"})
            if row["expires_at"] < self.now():
                connection.rollback()
                return LabResponse(410, {"error": "challenge_expired"})
            actual = (row["account_id"], row["ip"], row["device_id"], row["session_id"])
            expected = (account_id, ip, device_id, session_id)
            if actual != expected:
                connection.rollback()
                return LabResponse(403, {"error": "challenge_binding_mismatch"})
            updated = connection.execute(
                "UPDATE captcha_challenges SET used=1 WHERE token_hash=? AND used=0",
                (token_hash,),
            ).rowcount
            if updated != 1:
                connection.rollback()
                return LabResponse(409, {"error": "challenge_replayed"})
            grant = secrets.token_urlsafe(32)
            connection.execute(
                """
                INSERT INTO captcha_grants(
                    token_hash, account_id, ip, device_id, session_id, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    self._hash_token(grant),
                    account_id,
                    ip,
                    device_id,
                    session_id,
                    self.now() + 60,
                ),
            )
            self._audit(connection, "captcha_solved", account_id, {})
            connection.commit()
            return LabResponse(200, {"captcha_grant": grant, "expires_in": 60})

    def counter(self, dimension: str, identity: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT count FROM usage_counters WHERE dimension=? AND identity=? AND usage_day=?",
                (dimension, identity, self._day()),
            ).fetchone()
            return int(row["count"]) if row else 0

