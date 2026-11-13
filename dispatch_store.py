"""Durable, fail-closed one-time dispatch gate for MCP Apps UI messages.

A claim is irrevocable until an operator reconciles it. Returning "host_accepted"
does not prove that ChatGPT produced an answer. Never auto-retry ambiguous claims.
"""
import os
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path

DEFAULT_DB = Path(__file__).with_name("state") / "dispatch.sqlite3"
MAX_CLAIMS = 1  # This test is single-shot; future multi-turn experiments need a new workflow.


def initialize_database(path=DEFAULT_DB):
    """Explicit provisioning; never called automatically when handling a claim."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("""
          CREATE TABLE IF NOT EXISTS dispatch (
            event_id TEXT PRIMARY KEY,
            claim_token TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL CHECK (
              status IN ('claimed','host_accepted','host_rejected','unknown')
            ),
            claimed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            finalized_at TEXT,
            detail TEXT
          )
        """)
        connection.commit()
    finally:
        connection.close()
    os.chmod(path, 0o600)


def _connect_existing(path):
    """Do not silently create a new DB and erase deduplication history."""
    path = Path(path)
    if not path.is_file():
        raise RuntimeError("Dispatch ledger missing; automatic send must remain disabled")
    # mode=rw explicitly refuses a missing DB.
    connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=5)
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def claim_once(event_id, *, enabled=False, path=DEFAULT_DB):
    if not enabled:
        return {"granted": False, "reason": "disabled"}
    if not isinstance(event_id, str) or len(event_id) > 120 or not event_id:
        return {"granted": False, "reason": "invalid_event_id"}
    try:
        with closing(_connect_existing(path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT status FROM dispatch WHERE event_id=?", (event_id,)
            ).fetchone()
            if existing:
                return {"granted": False, "reason": "already_claimed",
                        "status": existing[0]}
            count = db.execute("SELECT COUNT(*) FROM dispatch").fetchone()[0]
            if count >= MAX_CLAIMS:
                return {"granted": False, "reason": "global_limit"}
            token = secrets.token_urlsafe(24)
            db.execute("INSERT INTO dispatch(event_id,claim_token,status)"
                       " VALUES(?,?,'claimed')", (event_id, token))
            return {"granted": True, "claim_token": token, "status": "claimed"}
    except (sqlite3.Error, OSError, RuntimeError) as exc:
        return {"granted": False, "reason": "ledger_unavailable",
                "detail": type(exc).__name__}


def report_result(event_id, token, outcome, *, path=DEFAULT_DB):
    """Record host-level result only. Reporting is idempotent; it never reclaims."""
    if outcome not in ("host_accepted", "host_rejected", "unknown"):
        return {"recorded": False, "reason": "invalid_outcome"}
    if not isinstance(token, str) or not token:
        return {"recorded": False, "reason": "invalid_token"}
    try:
        with closing(_connect_existing(path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM dispatch WHERE event_id=?"
                             " AND claim_token=?", (event_id, token)).fetchone()
            if not row:
                return {"recorded": False, "reason": "no_matching_claim"}
            if row[0] == outcome:
                return {"recorded": True, "status": outcome, "idempotent": True}
            if row[0] != "claimed":
                return {"recorded": False, "reason": "already_finalized",
                        "status": row[0]}
            db.execute("UPDATE dispatch SET status=?,finalized_at=CURRENT_TIMESTAMP"
                       " WHERE event_id=? AND claim_token=? AND status='claimed'",
                       (outcome, event_id, token))
            return {"recorded": True, "status": outcome}
    except (sqlite3.Error, OSError, RuntimeError) as exc:
        return {"recorded": False, "reason": "ledger_unavailable",
                "detail": type(exc).__name__}


def get_status(event_id, *, path=DEFAULT_DB):
    try:
        with closing(_connect_existing(path)) as db, db:
            row = db.execute("SELECT status,claimed_at,finalized_at FROM dispatch"
                             " WHERE event_id=?", (event_id,)).fetchone()
        if row is None:
            return {"status": "not_claimed"}
        return {"status": row[0], "claimed_at": row[1], "finalized_at": row[2]}
    except (sqlite3.Error, OSError, RuntimeError):
        return {"status": "ledger_unavailable"}
