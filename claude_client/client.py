"""Muse-side event client for the ChatGPT MCP Bridge (offline preparation).

Flow (per event):

1. ``prepare(message)``  -- allocate an event_id and record it locally.
2. A trusted operator on the bridge side authorizes that exact event_id
   (queue ``authorize``; never callable from this client).
3. ``send(event_id)``     -- exactly ONE enqueue attempt. If the transport
   result is ambiguous (exception/timeout), the event is recorded as
   ``enqueue_unknown`` and is never resent automatically; a human decides.
4. ``status(event_id)``   -- read queue status (read-only, repeatable).
5. ``cancel(event_id)``   -- allowed only before the UI claims the event.
6. ``receipt(event_id)``  -- read the PR #7-style response-receipt status
   (read-only). A receipt proves only that the fixed acknowledgement was
   recorded through the model-mediated path; it is best-effort, never a
   delivery guarantee.

The client never sends identity fields; the transport context is fixed by
the hosting code that constructs the transport.
"""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import time
import uuid

MAX_MESSAGE_BYTES = 768  # Must match the bridge queue limit (PR #9).
MAX_TTL_SECONDS = 300

_TERMINAL = frozenset({"queued", "enqueue_unknown", "rejected", "cancelled"})


class LocalTransport:
    """In-process transport: hands requests to a BridgeShim with a fixed
    transport context chosen by the hosting process (never by the caller).
    Stands in for the future SSH/Tailscale transports in offline tests."""

    def __init__(self, shim, transport_context):
        self._shim = shim
        self._context = transport_context
        self.calls = []  # test/diagnostic visibility into attempt counts

    def call(self, method, params):
        self.calls.append(method)
        return self._shim.handle(self._context, method, params)


def _private_db_path(path):
    path = Path(path).absolute()
    if path.is_symlink() or path.parent.is_symlink():
        raise RuntimeError("Symlink outbox path forbidden")
    parent = path.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    ps = parent.stat()
    if ps.st_uid != os.geteuid() or ps.st_mode & 0o077:
        raise PermissionError("Outbox parent must be owned by process, mode 0700")
    if not path.exists():
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    fs = path.stat()
    if fs.st_uid != os.geteuid() or fs.st_mode & 0o077:
        raise PermissionError("Outbox DB must be owned by process, mode 0600")
    return path


class Outbox:
    """Muse-local durable record of prepared/sent events (own SQLite file,
    deliberately separate from every bridge-side ledger)."""

    def __init__(self, path):
        self._path = _private_db_path(path)
        with closing(self._connect()) as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS outbox (
                    event_id TEXT PRIMARY KEY,
                    message TEXT NOT NULL,
                    state TEXT NOT NULL,
                    detail TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )""")
            db.commit()

    def _connect(self):
        db = sqlite3.connect(self._path.as_uri() + "?mode=rw", uri=True, timeout=5)
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def add(self, event_id, message):
        now = int(time.time())
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO outbox(event_id,message,state,created_at,updated_at)"
                " VALUES(?,?,'prepared',?,?)", (event_id, message, now, now))

    def get(self, event_id):
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT event_id,message,state,detail FROM outbox WHERE event_id=?",
                (event_id,)).fetchone()
        if row is None:
            return None
        return {"event_id": row[0], "message": row[1], "state": row[2], "detail": row[3]}

    def set_state(self, event_id, state, detail=None):
        with closing(self._connect()) as db, db:
            db.execute("UPDATE outbox SET state=?,detail=?,updated_at=? WHERE event_id=?",
                       (state, detail, int(time.time()), event_id))


class EventClient:
    """One-shot, no-retry client. ``transport`` is any object with
    ``call(method, params) -> dict`` following the BridgeShim response shape."""

    def __init__(self, transport, outbox):
        self._transport = transport
        self._outbox = outbox

    def prepare(self, message):
        if type(message) is not str or not message.strip() or "\x00" in message:
            raise ValueError("message must be a non-empty string without NUL")
        if len(message.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise ValueError("message exceeds the bridge queue byte limit")
        event_id = "muse-" + uuid.uuid4().hex
        self._outbox.add(event_id, message)
        return event_id

    def send(self, event_id):
        record = self._outbox.get(event_id)
        if record is None:
            return {"sent": False, "reason": "unknown_event"}
        if record["state"] != "prepared":
            # An attempt (or a terminal decision) already exists for this
            # event_id. Never place a second enqueue attempt automatically.
            return {"sent": False, "reason": "already_attempted",
                    "state": record["state"]}
        try:
            response = self._transport.call(
                "enqueue", {"event_id": event_id, "message": record["message"]})
        except Exception:
            self._outbox.set_state(event_id, "enqueue_unknown", "transport_error")
            return {"sent": False, "reason": "unknown", "state": "enqueue_unknown"}
        result = response.get("result") if isinstance(response, dict) else None
        if isinstance(result, dict) and result.get("queued"):
            self._outbox.set_state(event_id, "queued")
            return {"sent": True, "state": "queued"}
        reason = (result or {}).get("reason") or response.get("reason") or "rejected"
        if reason in ("expired", "already_used", "not_authorized", "invalid_arguments"):
            self._outbox.set_state(event_id, "rejected", reason)
            return {"sent": False, "reason": reason, "state": "rejected"}
        # Ambiguous bridge-side failure: treat like a transport unknown.
        self._outbox.set_state(event_id, "enqueue_unknown", str(reason))
        return {"sent": False, "reason": "unknown", "state": "enqueue_unknown"}

    def cancel(self, event_id):
        record = self._outbox.get(event_id)
        if record is None:
            return {"cancelled": False, "reason": "unknown_event"}
        if record["state"] == "prepared":
            # Never enqueued: local cancellation only, no transport call.
            self._outbox.set_state(event_id, "cancelled", "before_enqueue")
            return {"cancelled": True, "state": "cancelled", "scope": "local_only"}
        response = self._transport.call("cancel", {"event_id": event_id})
        result = response.get("result") if isinstance(response, dict) else None
        if isinstance(result, dict) and result.get("cancelled"):
            self._outbox.set_state(event_id, "cancelled")
            return {"cancelled": True, "state": "cancelled"}
        return {"cancelled": False,
                "reason": (result or {}).get("reason") or response.get("reason")}

    def status(self, event_id):
        response = self._transport.call("status", {"event_id": event_id})
        result = response.get("result") if isinstance(response, dict) else None
        if isinstance(result, dict):
            return {"ok": True, "queue_status": result.get("status")}
        return {"ok": False, "reason": response.get("reason", "shim_error")}

    def receipt(self, event_id):
        response = self._transport.call("receipt_status", {"event_id": event_id})
        result = response.get("result") if isinstance(response, dict) else None
        if isinstance(result, dict):
            return {"ok": True, "receipt_status": result.get("status")}
        return {"ok": False, "reason": response.get("reason", "shim_error")}
