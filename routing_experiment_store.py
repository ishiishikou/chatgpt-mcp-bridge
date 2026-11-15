"""A separate, lazy SQLite ledger: eight lifetime slots (mode x OS x visible/hidden), never regrant or reset.

No retired ledger imports or environment-controlled paths. App-only visibility is
a host permission boundary, not authentication against arbitrary MCP clients.
"""
import secrets
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

DB_NAME = "routing-experiment-v1.sqlite3"
DEFAULT_DB = Path(__file__).resolve().parent / "state" / DB_NAME
MODES = ("active", "new")
DEVICES = ("iOS", "Windows")
PHASES = ("visible", "hidden")
EVENTS = ("initialized", "timer_started", "visibilitychange", "pagehide", "teardown",
          "skipped", "claim_requested", "claim_denied", "message_attempt", "result")
OUTCOMES = ("unknown", "host_accepted", "host_rejected", "skipped")


def valid_run(run_id):
    try:
        return isinstance(run_id, str) and str(UUID(run_id, version=4)) == run_id
    except (ValueError, AttributeError):
        return False


def valid_time(value):
    try:
        return (isinstance(value, str) and len(value) <= 32 and
                datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is not None)
    except (ValueError, AttributeError):
        return False


def now():
    return datetime.now(timezone.utc).isoformat()


class RoutingStore:
    def __init__(self, path=DEFAULT_DB):
        self.path = Path(path).absolute()
        if self.path.name != DB_NAME:
            raise ValueError("Only the isolated routing experiment filename is allowed")

    @contextmanager
    def _transaction(self):
        # Refuse aliases that could lead to an existing/retired database.
        if self.path.parent.resolve() != self.path.parent or self.path.is_symlink():
            raise OSError("Ledger path must not contain symlinks")
        if self.path.exists() and self.path.stat().st_nlink != 1:
            raise OSError("Ledger must not be hardlinked")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("""CREATE TABLE IF NOT EXISTS claims (
                    mode TEXT NOT NULL CHECK(mode IN ('active','new')),
                    device TEXT NOT NULL CHECK(device IN ('iOS','Windows')),
                    phase TEXT NOT NULL CHECK(phase IN ('visible','hidden')),
                    run_id TEXT NOT NULL UNIQUE, generated_at TEXT NOT NULL,
                    claim_token TEXT NOT NULL UNIQUE, claimed_at TEXT NOT NULL,
                    outcome TEXT NOT NULL DEFAULT 'unknown', reported_at TEXT,
                    PRIMARY KEY(mode,device,phase))""")
                db.execute("""CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, run_id TEXT NOT NULL,
                    mode TEXT NOT NULL, device TEXT NOT NULL, event TEXT NOT NULL,
                    visibility TEXT NOT NULL, elapsed_ms INTEGER NOT NULL,
                    recorded_at TEXT NOT NULL)""")
                db.execute("CREATE INDEX IF NOT EXISTS events_run ON events(run_id)")
                yield db

    def claim(self, mode, device, run_id, generated_at, phase="visible"):
        if (mode not in MODES or device not in DEVICES or phase not in PHASES or
                not valid_run(run_id) or not valid_time(generated_at)):
            return {"granted": False, "reason": "invalid_identity"}
        token = secrets.token_hex(32)
        try:
            with self._transaction() as db:
                try:
                    db.execute("""INSERT INTO claims
                        (mode,device,phase,run_id,generated_at,claim_token,claimed_at)
                        VALUES (?,?,?,?,?,?,?)""", (mode, device, phase, run_id, generated_at, token, now()))
                except sqlite3.IntegrityError:
                    return {"granted": False, "reason": "already_claimed"}
            # COMMIT must complete before a grant is returned to the iframe.
            return {"granted": True, "claim_token": token, "run_id": run_id}
        except (OSError, sqlite3.Error):
            return {"granted": False, "reason": "storage_unavailable"}

    def report(self, claim_token, outcome):
        if (not isinstance(claim_token, str) or len(claim_token) != 64 or
                outcome not in OUTCOMES):
            return {"recorded": False, "reason": "invalid_result"}
        try:
            with self._transaction() as db:
                changed = db.execute("""UPDATE claims SET outcome=?, reported_at=?
                    WHERE claim_token=? AND reported_at IS NULL""", (outcome, now(), claim_token)).rowcount
            return {"recorded": changed == 1}
        except (OSError, sqlite3.Error):
            return {"recorded": False, "reason": "storage_unavailable"}

    def record(self, mode, device, run_id, event, visibility, elapsed_ms):
        if (mode not in MODES or device not in DEVICES or not valid_run(run_id) or
                event not in EVENTS or visibility not in ("visible", "hidden", "unknown") or
                type(elapsed_ms) is not int or not 0 <= elapsed_ms <= 604800000):
            return {"recorded": False, "reason": "invalid_event"}
        try:
            with self._transaction() as db:
                count = db.execute("SELECT COUNT(*) FROM events WHERE run_id=?", (run_id,)).fetchone()[0]
                total = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                if count >= 64 or total >= 4096:
                    return {"recorded": False, "reason": "log_limit"}
                db.execute("""INSERT INTO events
                    (run_id,mode,device,event,visibility,elapsed_ms,recorded_at)
                    VALUES (?,?,?,?,?,?,?)""", (run_id, mode, device, event, visibility, elapsed_ms, now()))
            return {"recorded": True}
        except (OSError, sqlite3.Error):
            return {"recorded": False, "reason": "storage_unavailable"}
