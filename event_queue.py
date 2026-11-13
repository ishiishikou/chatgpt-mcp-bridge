"""Offline, consent-bound event queue for a future MCP Apps bridge.

No HTTP/MCP routes, no ChatGPT access, no UI messages or timers are created here.
This is NOT authorization middleware: identity_resolver MUST obtain a verified
identity from the server's protected transport context, never from model args.
"""
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import os
import secrets
import sqlite3
import time

MAX_TOTAL_EVENTS = 1  # An entirely new reviewed protocol is needed for loops.
MAX_MESSAGE_BYTES = 768
MAX_TTL_SECONDS = 300
DEFAULT_DB = Path(__file__).with_name("state") / "event_queue.sqlite3"
OUTCOMES = frozenset({"host_accepted", "host_rejected", "unknown"})


@dataclass(frozen=True)
class VerifiedEventIdentity:
    subject: str
    owner: str
    session: str
    source: str
    scopes: frozenset[str]


def _identifier(value):
    return (type(value) is str and 0 < len(value) <= 120
            and value.isascii()
            and all(c.isalnum() or c in "-_:" for c in value))


def _private_path(path):
    path = Path(path).absolute()
    if path.is_symlink() or path.parent.is_symlink():
        raise RuntimeError("Symlink queue path forbidden")
    parent = path.parent
    ps = parent.stat()
    if not parent.is_dir() or ps.st_uid != os.geteuid() or ps.st_mode & 0o077:
        raise PermissionError("Queue parent must be owned by process and mode 0700")
    if not path.is_file():
        raise RuntimeError("Queue DB not provisioned")
    fs = path.stat()
    if fs.st_uid != os.geteuid() or fs.st_mode & 0o077:
        raise PermissionError("Queue DB must be owned by process and mode 0600")
    return path


def initialize_queue(path=DEFAULT_DB):
    """Explicit *offline operator* provisioning, never an incoming tool call."""
    path = Path(path).absolute()
    if path.is_symlink() or path.parent.is_symlink():
        raise RuntimeError("Symlink queue path forbidden")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists():
        _private_path(path)
    else:
        parent = path.parent.stat()
        if parent.st_uid != os.geteuid() or parent.st_mode & 0o077:
            raise PermissionError("Queue parent must be private")
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        os.close(fd)
        _private_path(path)
    with closing(sqlite3.connect(path, timeout=5)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("""
            CREATE TABLE IF NOT EXISTS event_queue (
                event_id TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                session TEXT NOT NULL,
                source TEXT NOT NULL,
                message TEXT,
                status TEXT NOT NULL CHECK(status IN (
                    'authorized','queued','claimed','host_accepted',
                    'host_rejected','unknown','cancelled')),
                expires_at INTEGER NOT NULL,
                claim_token TEXT UNIQUE,
                created_at INTEGER NOT NULL,
                claimed_at INTEGER,
                finalized_at INTEGER
            )
        """)
        db.commit()


def _existing(path):
    path = _private_path(path)
    db = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=5)
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("PRAGMA synchronous=FULL")
    return db


class OfflineEventQueue:
    """One-off queue; only the embedding server can resolve trusted identities."""

    def __init__(self, identity_resolver, *, path=DEFAULT_DB, enabled=False,
                 allowed_sources=frozenset({"muse", "codex"})):
        if not callable(identity_resolver):
            raise TypeError("Identity resolver must be callable")
        if type(enabled) is not bool:
            raise TypeError("enabled must be bool")
        if (not isinstance(allowed_sources, frozenset)
                or not allowed_sources
                or not all(_identifier(s) for s in allowed_sources)):
            raise ValueError("allowed_sources must be a nonempty frozenset")
        self._resolve = identity_resolver
        self._path = Path(path)
        self._enabled = enabled
        self._sources = allowed_sources

    def _identity(self, context, scope):
        if not self._enabled:
            return None
        try:
            identity = self._resolve(context)
        except Exception:
            return None
        if (not isinstance(identity, VerifiedEventIdentity)
                or any(not _identifier(x) for x in (
                    identity.subject, identity.owner, identity.session, identity.source))
                or not isinstance(identity.scopes, frozenset)
                or not all(type(s) is str for s in identity.scopes)
                or scope not in identity.scopes):
            return None
        return identity

    def authorize(self, trusted_context, event_id, source, *, ttl_seconds=300):
        """Trusted, explicitly approved operator step. Never expose to model."""
        identity = self._identity(trusted_context, "bridge:event:authorize")
        if identity is None:
            return {"authorized": False, "reason": "not_authorized"}
        if (not _identifier(event_id) or not _identifier(source)
                or source not in self._sources
                or type(ttl_seconds) is not int
                or not 1 <= ttl_seconds <= MAX_TTL_SECONDS):
            return {"authorized": False, "reason": "invalid_arguments"}
        try:
            with closing(_existing(self._path)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                now = int(time.time())
                if db.execute("SELECT 1 FROM event_queue WHERE event_id=?", (event_id,)).fetchone():
                    return {"authorized": False, "reason": "duplicate"}
                if db.execute("SELECT COUNT(*) FROM event_queue").fetchone()[0] >= MAX_TOTAL_EVENTS:
                    return {"authorized": False, "reason": "global_limit"}
                db.execute(
                    "INSERT INTO event_queue(event_id,owner,session,source,status,expires_at,created_at)"
                    " VALUES(?,?,?,?,'authorized',?,?)",
                    (event_id, identity.owner, identity.session, source, now + ttl_seconds, now))
            return {"authorized": True, "status": "authorized"}
        except (sqlite3.Error, OSError, RuntimeError, PermissionError):
            return {"authorized": False, "reason": "ledger_unavailable"}

    def enqueue(self, trusted_context, event_id, message):
        """Source comes exclusively from authenticated context, not arguments."""
        identity = self._identity(trusted_context, "bridge:event:enqueue")
        if identity is None or identity.source not in self._sources:
            return {"queued": False, "reason": "not_authorized"}
        if not _identifier(event_id) or type(message) is not str:
            return {"queued": False, "reason": "invalid_arguments"}
        try:
            if (not message.strip() or "\x00" in message
                    or len(message.encode("utf-8")) > MAX_MESSAGE_BYTES):
                return {"queued": False, "reason": "invalid_arguments"}
        except UnicodeError:
            return {"queued": False, "reason": "invalid_arguments"}
        try:
            with closing(_existing(self._path)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                now = int(time.time())
                row = db.execute(
                    "SELECT owner,session,source,status,expires_at FROM event_queue WHERE event_id=?",
                    (event_id,)).fetchone()
                if not row or row[:3] != (identity.owner, identity.session, identity.source):
                    return {"queued": False, "reason": "not_authorized"}
                if now >= row[4]:
                    return {"queued": False, "reason": "expired"}
                if row[3] != "authorized":
                    return {"queued": False, "reason": "already_used"}
                db.execute("UPDATE event_queue SET status='queued', message=? WHERE event_id=?",
                           (message, event_id))
            return {"queued": True, "status": "queued"}
        except (sqlite3.Error, OSError, RuntimeError, PermissionError):
            return {"queued": False, "reason": "ledger_unavailable"}

    def claim(self, trusted_context, event_id):
        """At-most-once claim; claimed/unknown events are *never* reissued."""
        identity = self._identity(trusted_context, "bridge:event:claim")
        if identity is None or not _identifier(event_id):
            return {"claimed": False, "reason": "not_authorized"}
        try:
            with closing(_existing(self._path)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                now = int(time.time())
                row = db.execute(
                    "SELECT owner,session,status,expires_at,message FROM event_queue WHERE event_id=?",
                    (event_id,)).fetchone()
                if not row or row[:2] != (identity.owner, identity.session):
                    return {"claimed": False, "reason": "not_authorized"}
                if now >= row[3]:
                    return {"claimed": False, "reason": "expired"}
                if row[2] != "queued":
                    return {"claimed": False, "reason": "not_queued"}
                token = secrets.token_urlsafe(32)
                db.execute(
                    "UPDATE event_queue SET status='claimed', claim_token=?,claimed_at=? WHERE event_id=?",
                    (token, now, event_id))
            return {"claimed": True, "status": "claimed", "claim_token": token, "message": row[4]}
        except (sqlite3.Error, OSError, RuntimeError, PermissionError):
            return {"claimed": False, "reason": "ledger_unavailable"}

    def report(self, trusted_context, event_id, claim_token, outcome):
        """Host receipt only; does not prove the assistant answered."""
        identity = self._identity(trusted_context, "bridge:event:report")
        if identity is None or not _identifier(event_id):
            return {"recorded": False, "reason": "not_authorized"}
        if (type(claim_token) is not str or len(claim_token) < 32
                or not claim_token.isascii()
                or type(outcome) is not str or outcome not in OUTCOMES):
            return {"recorded": False, "reason": "invalid_arguments"}
        now = int(time.time())
        try:
            with closing(_existing(self._path)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT owner,session,status,claim_token FROM event_queue WHERE event_id=?",
                    (event_id,)).fetchone()
                if (not row or row[:2] != (identity.owner, identity.session)
                        or not isinstance(row[3], str)
                        or not secrets.compare_digest(row[3], claim_token)):
                    return {"recorded": False, "reason": "not_authorized"}
                if row[2] == outcome:
                    return {"recorded": True, "status": outcome, "idempotent": True}
                if row[2] != "claimed":
                    return {"recorded": False, "reason": "already_finalized"}
                db.execute("UPDATE event_queue SET status=?,finalized_at=? WHERE event_id=?",
                           (outcome, now, event_id))
            return {"recorded": True, "status": outcome}
        except (sqlite3.Error, OSError, RuntimeError, PermissionError):
            return {"recorded": False, "reason": "ledger_unavailable"}

    def cancel(self, trusted_context, event_id):
        """Only before claim. A claimed dispatch cannot be revoked remotely."""
        identity = self._identity(trusted_context, "bridge:event:cancel")
        if identity is None or not _identifier(event_id):
            return {"cancelled": False, "reason": "not_authorized"}
        try:
            with closing(_existing(self._path)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT owner,session,status FROM event_queue WHERE event_id=?",
                    (event_id,)).fetchone()
                if not row or row[:2] != (identity.owner, identity.session):
                    return {"cancelled": False, "reason": "not_authorized"}
                if row[2] not in ("authorized", "queued"):
                    return {"cancelled": False, "reason": "too_late_or_finalized"}
                db.execute("UPDATE event_queue SET status='cancelled',finalized_at=? WHERE event_id=?",
                           (int(time.time()), event_id))
            return {"cancelled": True, "status": "cancelled"}
        except (sqlite3.Error, OSError, RuntimeError, PermissionError):
            return {"cancelled": False, "reason": "ledger_unavailable"}

    def status(self, trusted_context, event_id):
        """Owner/session-scoped metadata only; message and token never returned."""
        identity = self._identity(trusted_context, "bridge:event:read")
        if identity is None or not _identifier(event_id):
            return {"status": "not_authorized"}
        try:
            with closing(_existing(self._path)) as db:
                row = db.execute(
                    "SELECT owner,session,status,expires_at FROM event_queue WHERE event_id=?",
                    (event_id,)).fetchone()
            if not row or row[:2] != (identity.owner, identity.session):
                return {"status": "not_authorized"}
            if row[2] in ("authorized", "queued") and int(time.time()) >= row[3]:
                return {"status": "expired"}
            return {"status": row[2]}
        except (sqlite3.Error, OSError, RuntimeError, PermissionError):
            return {"status": "ledger_unavailable"}
