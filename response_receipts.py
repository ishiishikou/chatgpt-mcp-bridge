"""Offline prototype: authenticated, fixed-phrase receipt storage.

NOT a passive ChatGPT conversation reader. NOT registered as an MCP tool.
Integration requires a verified host-supplied principal and explicit test approval.
"""
from contextlib import closing
import os
from pathlib import Path
import secrets
import sqlite3
import time

DEFAULT_DB = Path(__file__).with_name("state") / "response_receipts.sqlite3"
ACK_PHRASE = "安全送信を受信"
MAX_EVENTS = 1


def initialize_receipts(path=DEFAULT_DB):
    """Explicit operator-only provisioning; no shared-writeable or symlink paths."""
    raw_path = Path(path)
    if raw_path.is_symlink():
        raise RuntimeError("Symlink receipt database path forbidden")
    path = raw_path.resolve()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.stat().st_mode & 0o077:
        raise PermissionError("Receipt database directory must be private (0700)")
    if not path.exists():
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    elif not path.is_file():
        raise RuntimeError("Receipt database must be a regular file")
    os.chmod(path, 0o600)
    with closing(sqlite3.connect(path, timeout=5)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("""
            CREATE TABLE IF NOT EXISTS receipt (
                event_id TEXT PRIMARY KEY,
                owner_principal TEXT NOT NULL,
                expected_phrase TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending','received')),
                recorded_at INTEGER
            )
        """)
        db.commit()


def _existing(path):
    raw_path = Path(path)
    if raw_path.is_symlink():
        raise RuntimeError("Symlink receipt database path forbidden")
    path = raw_path.resolve()
    if (not path.is_file() or path.parent.stat().st_mode & 0o077
            or path.stat().st_mode & 0o077):
        raise RuntimeError("Receipt DB missing or file/directory not private")
    db = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=5)
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("PRAGMA synchronous=FULL")
    return db


def _identifier(text, limit=120):
    return isinstance(text, str) and 0 < len(text) <= limit and text.isascii() and all(
        c.isalnum() or c in "-_:" for c in text
    )


def prepare_receipt(event_id, owner_principal, *, enabled=False, ttl_seconds=300,
                    path=DEFAULT_DB, now=None):
    """Trusted-operator setup; never expose as a model-callable tool."""
    if not enabled:
        return {"prepared": False, "reason": "disabled"}
    if not _identifier(event_id) or not _identifier(owner_principal):
        return {"prepared": False, "reason": "invalid_identifier"}
    if not isinstance(ttl_seconds, int) or not 1 <= ttl_seconds <= 3600:
        return {"prepared": False, "reason": "invalid_ttl"}
    if now is not None and (type(now) is not int or now < 0):
        return {"prepared": False, "reason": "invalid_clock"}
    clock = int(time.time()) if now is None else now
    try:
        with closing(_existing(path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM receipt WHERE event_id=?", (event_id,)).fetchone():
                return {"prepared": False, "reason": "already_prepared"}
            if db.execute("SELECT COUNT(*) FROM receipt").fetchone()[0] >= MAX_EVENTS:
                return {"prepared": False, "reason": "global_limit"}
            db.execute(
                "INSERT INTO receipt(event_id,owner_principal,expected_phrase,expires_at,status) "
                "VALUES(?,?,?,?,'pending')",
                (event_id, owner_principal, ACK_PHRASE, clock + ttl_seconds),
            )
            return {"prepared": True, "status": "pending"}
    except (sqlite3.Error, OSError, RuntimeError) as exc:
        return {"prepared": False, "reason": "ledger_unavailable",
                "error_class": type(exc).__name__}


def record_receipt(event_id, phrase, *, authenticated_principal=None,
                   enabled=False, path=DEFAULT_DB, now=None):
    """Caller MUST derive authenticated_principal from trusted transport identity.

    Never take authenticated_principal from a model-supplied MCP argument.
    Never auto-enable. Only a predetermined, non-sensitive acknowledgment is allowed.
    """
    if not enabled:
        return {"recorded": False, "reason": "disabled"}
    if not _identifier(event_id) or not _identifier(authenticated_principal):
        return {"recorded": False, "reason": "not_authorized"}
    if phrase != ACK_PHRASE:
        return {"recorded": False, "reason": "unexpected_phrase"}
    if now is not None and (type(now) is not int or now < 0):
        return {"recorded": False, "reason": "invalid_clock"}
    clock = int(time.time()) if now is None else now
    try:
        with closing(_existing(path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT owner_principal,expected_phrase,expires_at,status "
                "FROM receipt WHERE event_id=?", (event_id,),
            ).fetchone()
            if not row or not secrets.compare_digest(row[0], authenticated_principal):
                return {"recorded": False, "reason": "not_authorized"}
            _, expected, deadline, status = row
            if status == "received":
                return {"recorded": False, "reason": "already_received"}
            if clock >= deadline:
                return {"recorded": False, "reason": "expired"}
            if not secrets.compare_digest(expected.encode('utf-8'), phrase.encode('utf-8')):
                return {"recorded": False, "reason": "unexpected_phrase"}
            db.execute(
                "UPDATE receipt SET status='received', recorded_at=? "
                "WHERE event_id=? AND status='pending'",
                (clock, event_id),
            )
            return {"recorded": True, "status": "received"}
    except (sqlite3.Error, OSError, RuntimeError) as exc:
        return {"recorded": False, "reason": "ledger_unavailable",
                "error_class": type(exc).__name__}


def get_receipt_status(event_id, *, authenticated_principal=None, path=DEFAULT_DB):
    """Read with the same host-authenticated identity; never return reply text."""
    if not _identifier(event_id) or not _identifier(authenticated_principal):
        return {"status": "not_authorized"}
    try:
        with closing(_existing(path)) as db:
            row = db.execute("SELECT owner_principal,status FROM receipt WHERE event_id=?",
                             (event_id,)).fetchone()
        if not row or not secrets.compare_digest(row[0], authenticated_principal):
            return {"status": "not_authorized"}
        return {"status": row[1]}
    except (sqlite3.Error, OSError, RuntimeError):
        return {"status": "ledger_unavailable"}