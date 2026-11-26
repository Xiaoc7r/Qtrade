"""Durable intent/outbox and audit log, not a broker cash/fills ledger."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from .models import Intent, Policy


class Store:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(
            directory / "execution.sqlite", timeout=5, check_same_thread=False
        )
        self.db.row_factory = sqlite3.Row
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            self.db.close()
            raise ValueError("未知的交易执行数据版本，停止执行")
        self.db.executescript("""
            PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS intents (
                client_id TEXT PRIMARY KEY, payload TEXT NOT NULL, state TEXT NOT NULL,
                broker_id TEXT, message TEXT NOT NULL, updated_at TEXT NOT NULL,
                day TEXT NOT NULL, attempted INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS ix_intent_day ON intents(day, attempted);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS days (
                day TEXT PRIMARY KEY, anchor REAL NOT NULL, ai_calls INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS broker_receipts (
                receipt_key TEXT PRIMARY KEY, at TEXT NOT NULL, payload TEXT NOT NULL
            );
        """)
        with self.db:
            self.db.execute("PRAGMA user_version=1")
            self.db.execute(
                "UPDATE intents SET state='unknown', message='重启恢复：需券商正向回报对账' WHERE state='submitting'"
            )

    def close(self):
        with self.lock:
            self.db.close()

    def get(self, key, default=None):
        with self.lock:
            row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def put(self, key, value):
        with self.lock, self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO meta VALUES (?,?)",
                (key, json.dumps(value, allow_nan=False)),
            )

    def policy(self):
        return Policy.model_validate(self.get("policy", {}))

    def audit(self, at, kind, body):
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO events(at,kind,body) VALUES (?,?,?)",
                (at.isoformat(), kind, json.dumps(body, ensure_ascii=False, allow_nan=False)),
            )

    def events(self, limit=100):
        with self.lock:
            return [
                {**dict(row), "body": json.loads(row["body"])}
                for row in self.db.execute(
                    "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
                )
            ]

    def records(self, limit=200):
        with self.lock:
            return [
                self._record(row)
                for row in self.db.execute(
                    "SELECT * FROM intents ORDER BY updated_at DESC LIMIT ?", (limit,)
                )
            ]

    def lookup(self, client_id):
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM intents WHERE client_id=?", (client_id,)
            ).fetchone()
            return self._record(row) if row else None

    def active(self):
        with self.lock:
            return [
                self._record(row)
                for row in self.db.execute(
                    "SELECT * FROM intents WHERE attempted=1 AND state NOT IN ('filled','cancelled','rejected')"
                )
            ]

    def daily(self, day):
        with self.lock:
            return [
                self._record(row)
                for row in self.db.execute(
                    "SELECT * FROM intents WHERE day=? AND attempted=1", (day.isoformat(),)
                )
            ]

    @staticmethod
    def _record(row):
        return {**dict(row), "intent": json.loads(row["payload"])}

    def plan(self, intent: Intent, state: str, message: str, now):
        with self.lock, self.db:
            self.db.execute(
                """INSERT INTO intents(client_id,payload,state,message,updated_at,day)
                VALUES (?,?,?,?,?,?) ON CONFLICT(client_id) DO UPDATE SET payload=excluded.payload,
                state=excluded.state,message=excluded.message,updated_at=excluded.updated_at
                WHERE intents.attempted=0""",
                (
                    intent.client_id,
                    intent.model_dump_json(),
                    state,
                    message,
                    now.isoformat(),
                    now.date().isoformat(),
                ),
            )

    def claim(self, client_id, now) -> bool:
        # Commit the submission fence BEFORE touching the broker. Never re-claim an attempted intent.
        with self.lock, self.db:
            return (
                self.db.execute(
                    "UPDATE intents SET attempted=1,state='submitting',updated_at=? WHERE client_id=? AND attempted=0 AND state='planned'",
                    (now.isoformat(), client_id),
                ).rowcount
                == 1
            )

    def update(self, client_id, state, message, now, broker_id=None):
        with self.lock, self.db:
            self.db.execute(
                "UPDATE intents SET state=?,message=?,updated_at=?,broker_id=COALESCE(?,broker_id) WHERE client_id=?",
                (state, message, now.isoformat(), broker_id, client_id),
            )

    def anchor(self, day, equity):
        with self.lock, self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO days(day,anchor) VALUES (?,?)", (day.isoformat(), equity)
            )
            self.db.execute(
                "UPDATE days SET anchor=? WHERE day=? AND anchor=0", (equity, day.isoformat())
            )
            return self.db.execute(
                "SELECT anchor FROM days WHERE day=?", (day.isoformat(),)
            ).fetchone()[0]

    def claim_ai_call(self, day, limit):
        with self.lock, self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO days(day,anchor) VALUES (?,0)", (day.isoformat(),)
            )
            return (
                self.db.execute(
                    "UPDATE days SET ai_calls=ai_calls+1 WHERE day=? AND ai_calls<?",
                    (day.isoformat(), limit),
                ).rowcount
                == 1
            )

    def receipts(self, snapshot):
        with self.lock, self.db:
            for trade in snapshot.trades:
                key = f"{snapshot.fingerprint}:{snapshot.at.date()}:{trade.trade_id}"
                payload = trade.model_dump_json()
                old = self.db.execute(
                    "SELECT payload FROM broker_receipts WHERE receipt_key=?", (key,)
                ).fetchone()
                if old and old[0] != payload:
                    raise ValueError("同一券商成交回报内容不一致，停止执行")
                self.db.execute(
                    "INSERT OR IGNORE INTO broker_receipts VALUES (?,?,?)",
                    (key, snapshot.at.isoformat(), payload),
                )
