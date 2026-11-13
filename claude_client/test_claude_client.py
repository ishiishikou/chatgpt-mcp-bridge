"""Offline tests for the Muse-side bridge client.

Uses a faithful in-memory double of the PR #9 OfflineEventQueue state machine
(authorize -> enqueue -> claim -> report, cancel before claim, TTL, global
cap, owner/session/source binding). No network, no ChatGPT, no real ledger.

Run from the repository root:
    python -m unittest claude_client.test_claude_client -v
"""
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_client.client import EventClient, LocalTransport, Outbox
from claude_client.identity import Identity, TokenRegistryResolver, TailscaleContextResolver
from claude_client.shim import BridgeShim, serve_stdio

ALL_SCOPES = frozenset({
    "bridge:event:authorize", "bridge:event:enqueue", "bridge:event:claim",
    "bridge:event:report", "bridge:event:cancel", "bridge:event:read",
})
CLAUDE = Identity(subject="muse-vm", owner="user", session="s-1",
                  source="muse", scopes=ALL_SCOPES)
UI = Identity(subject="chatgpt-ui", owner="user", session="s-1",
              source="chatgpt-ui", scopes=ALL_SCOPES)
TOKEN = "test-token-do-not-use-in-production"
REGISTRY = {hashlib.sha256(TOKEN.encode()).hexdigest(): {
    "subject": CLAUDE.subject, "owner": CLAUDE.owner, "session": CLAUDE.session,
    "source": CLAUDE.source, "scopes": sorted(ALL_SCOPES)}}


class FakeQueue:
    """PR #9 semantics, in memory. Identity resolver decides bindings."""

    def __init__(self, resolver, *, enabled=True, allowed_sources=frozenset({"muse", "codex"}),
                 max_total=1, clock=None):
        self._resolve = resolver
        self._enabled = enabled
        self._sources = allowed_sources
        self._max_total = max_total
        self._clock = clock or (lambda: int(time.time()))
        self.rows = {}

    def _id(self, ctx, scope):
        if not self._enabled:
            return None
        ident = self._resolve(ctx)
        if ident is None or scope not in ident.scopes:
            return None
        return ident

    def authorize(self, ctx, event_id, source, *, ttl_seconds=300):
        ident = self._id(ctx, "bridge:event:authorize")
        if ident is None or source not in self._sources:
            return {"authorized": False, "reason": "not_authorized"}
        if event_id in self.rows:
            return {"authorized": False, "reason": "duplicate"}
        if len(self.rows) >= self._max_total:
            return {"authorized": False, "reason": "global_limit"}
        self.rows[event_id] = {
            "owner": ident.owner, "session": ident.session, "source": source,
            "status": "authorized", "expires_at": self._clock() + ttl_seconds,
            "message": None, "claim_token": None}
        return {"authorized": True, "status": "authorized"}

    def enqueue(self, ctx, event_id, message):
        ident = self._id(ctx, "bridge:event:enqueue")
        if ident is None or ident.source not in self._sources:
            return {"queued": False, "reason": "not_authorized"}
        row = self.rows.get(event_id)
        if not row or (row["owner"], row["session"], row["source"]) != (
                ident.owner, ident.session, ident.source):
            return {"queued": False, "reason": "not_authorized"}
        if self._clock() >= row["expires_at"]:
            return {"queued": False, "reason": "expired"}
        if row["status"] != "authorized":
            return {"queued": False, "reason": "already_used"}
        row["status"] = "queued"
        row["message"] = message
        return {"queued": True, "status": "queued"}

    def claim(self, ctx, event_id):
        ident = self._id(ctx, "bridge:event:claim")
        if ident is None:
            return {"claimed": False, "reason": "not_authorized"}
        row = self.rows.get(event_id)
        if not row or (row["owner"], row["session"]) != (ident.owner, ident.session):
            return {"claimed": False, "reason": "not_authorized"}
        if self._clock() >= row["expires_at"]:
            return {"claimed": False, "reason": "expired"}
        if row["status"] != "queued":
            return {"claimed": False, "reason": "not_queued"}
        row["status"] = "claimed"
        row["claim_token"] = secrets.token_urlsafe(32)
        return {"claimed": True, "claim_token": row["claim_token"], "message": row["message"]}

    def report(self, ctx, event_id, claim_token, outcome):
        ident = self._id(ctx, "bridge:event:report")
        if ident is None:
            return {"recorded": False, "reason": "not_authorized"}
        row = self.rows.get(event_id)
        if not row or row["status"] != "claimed" or row["claim_token"] != claim_token:
            return {"recorded": False, "reason": "not_authorized"}
        row["status"] = outcome
        return {"recorded": True, "status": outcome}

    def cancel(self, ctx, event_id):
        ident = self._id(ctx, "bridge:event:cancel")
        if ident is None:
            return {"cancelled": False, "reason": "not_authorized"}
        row = self.rows.get(event_id)
        if not row or (row["owner"], row["session"]) != (ident.owner, ident.session):
            return {"cancelled": False, "reason": "not_authorized"}
        if row["status"] not in ("authorized", "queued"):
            return {"cancelled": False, "reason": "too_late_or_finalized"}
        row["status"] = "cancelled"
        return {"cancelled": True, "status": "cancelled"}

    def status(self, ctx, event_id):
        ident = self._id(ctx, "bridge:event:read")
        if ident is None:
            return {"status": "not_authorized"}
        row = self.rows.get(event_id)
        if not row or (row["owner"], row["session"]) != (ident.owner, ident.session):
            return {"status": "not_authorized"}
        if row["status"] in ("authorized", "queued") and self._clock() >= row["expires_at"]:
            return {"status": "expired"}
        return {"status": row["status"]}


def make_registry_file(directory):
    path = Path(directory) / "registry.json"
    path.write_text(json.dumps(REGISTRY), encoding="utf-8")
    os.chmod(path, 0o600)
    return path


class ClientTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        os.chmod(base, 0o700)
        self.resolver = TokenRegistryResolver(make_registry_file(base))
        self.receipts = {}
        self.queue = FakeQueue(self.resolver)
        self.shim = BridgeShim(
            self.queue, self.resolver,
            receipt_status_fn=lambda ctx, eid: {"status": self.receipts.get(eid, "pending")})
        self.ctx = {"presented_token": TOKEN}
        self.transport = LocalTransport(self.shim, self.ctx)
        self.client = EventClient(self.transport, Outbox(base / "state" / "outbox.sqlite3"))
        # Bridge-side operator/UI context resolves through the same token for
        # the fake; UI-side ops use a resolver that returns the UI identity.
        self.ui_resolver = lambda ctx: UI if ctx == self.ctx else None

    def authorize(self, event_id, ttl=300):
        return self.queue.authorize(self.ctx, event_id, "muse", ttl_seconds=ttl)

    def test_happy_path_enqueue_claim_report_receipt(self):
        event_id = self.client.prepare("受信確認をお願いします")
        self.assertTrue(self.authorize(event_id)["authorized"])
        self.assertEqual(self.client.send(event_id), {"sent": True, "state": "queued"})
        self.assertEqual(self.client.status(event_id)["queue_status"], "queued")
        ui_queue = FakeQueue(self.ui_resolver)
        ui_queue.rows = self.queue.rows  # same ledger, UI-side view
        claimed = ui_queue.claim(self.ctx, event_id)
        self.assertTrue(claimed["claimed"])
        self.assertEqual(claimed["message"], "受信確認をお願いします")
        self.assertFalse(ui_queue.claim(self.ctx, event_id)["claimed"])  # at-most-once
        self.assertTrue(ui_queue.report(self.ctx, event_id, claimed["claim_token"],
                                        "host_accepted")["recorded"])
        self.assertEqual(self.client.status(event_id)["queue_status"], "host_accepted")
        self.receipts[event_id] = "received"
        self.assertEqual(self.client.receipt(event_id)["receipt_status"], "received")

    def test_send_before_authorize_is_rejected_not_retried(self):
        event_id = self.client.prepare("hello")
        result = self.client.send(event_id)
        self.assertEqual(result["state"], "rejected")
        self.assertEqual(self.transport.calls.count("enqueue"), 1)
        again = self.client.send(event_id)
        self.assertEqual(again["reason"], "already_attempted")
        self.assertEqual(self.transport.calls.count("enqueue"), 1)  # still one attempt

    def test_transport_failure_becomes_unknown_and_never_resends(self):
        class FlakyShim:
            def __init__(self, inner): self._inner = inner
            def handle(self, ctx, method, params):
                if method == "enqueue":
                    raise TimeoutError("ambiguous")
                return self._inner.handle(ctx, method, params)
        transport = LocalTransport(FlakyShim(self.shim), self.ctx)
        client = EventClient(transport, self.client._outbox)
        event_id = client.prepare("hello")
        self.assertTrue(self.authorize(event_id)["authorized"])
        result = client.send(event_id)
        self.assertEqual(result["state"], "enqueue_unknown")
        again = client.send(event_id)
        self.assertEqual(again["reason"], "already_attempted")
        self.assertEqual(transport.calls.count("enqueue"), 1)

    def test_wrong_token_cannot_enqueue(self):
        bad_transport = LocalTransport(self.shim, {"presented_token": "wrong"})
        client = EventClient(bad_transport, self.client._outbox)
        event_id = client.prepare("hello")
        self.assertTrue(self.authorize(event_id)["authorized"])
        self.assertEqual(client.send(event_id)["state"], "rejected")

    def test_identity_in_payload_is_rejected_by_shim(self):
        response = self.shim.handle(self.ctx, "enqueue", {
            "event_id": "x", "message": "hi", "owner": "attacker", "source": "codex"})
        self.assertEqual(response, {"ok": False, "reason": "identity_in_payload"})

    def test_authorize_and_claim_are_not_on_client_surface(self):
        for method in ("authorize", "claim", "report"):
            response = self.shim.handle(self.ctx, method, {"event_id": "x"})
            self.assertEqual(response["reason"], "method_not_exposed")

    def test_cancel_before_claim_and_too_late_after_claim(self):
        event_id = self.client.prepare("cancel me")
        self.assertTrue(self.authorize(event_id)["authorized"])
        self.assertTrue(self.client.send(event_id)["sent"])
        self.assertTrue(self.client.cancel(event_id)["cancelled"])
        event_id2 = self.client.prepare("too late")
        self.queue.rows.clear()  # new one-event budget for the second scenario
        self.assertTrue(self.authorize(event_id2)["authorized"])
        self.assertTrue(self.client.send(event_id2)["sent"])
        ui_queue = FakeQueue(self.ui_resolver)
        ui_queue.rows = self.queue.rows
        self.assertTrue(ui_queue.claim(self.ctx, event_id2)["claimed"])
        self.assertFalse(self.client.cancel(event_id2)["cancelled"])

    def test_expired_event_cannot_be_sent(self):
        now = [int(time.time())]
        queue = FakeQueue(self.resolver, clock=lambda: now[0])
        shim = BridgeShim(queue, self.resolver)
        client = EventClient(LocalTransport(shim, self.ctx), self.client._outbox)
        event_id = client.prepare("short lived")
        self.assertTrue(queue.authorize(self.ctx, event_id, "muse", ttl_seconds=60)["authorized"])
        now[0] += 61
        result = client.send(event_id)
        self.assertEqual(result["reason"], "expired")
        self.assertEqual(result["state"], "rejected")

    def test_global_cap_blocks_second_authorization(self):
        first = self.client.prepare("one")
        second = self.client.prepare("two")
        self.assertTrue(self.authorize(first)["authorized"])
        self.assertEqual(self.authorize(second)["reason"], "global_limit")

    def test_oversize_message_rejected_client_side(self):
        with self.assertRaises(ValueError):
            self.client.prepare("a" * 769)
        with self.assertRaises(ValueError):
            self.client.prepare("あ" * 257)  # 771 UTF-8 bytes

    def test_stdio_rejects_context_injection(self):
        lines = io.StringIO(json.dumps({
            "method": "status", "params": {"event_id": "x"},
            "context": {"presented_token": TOKEN}}) + "\n")
        out = io.StringIO()
        serve_stdio(self.shim, lines, out, self.ctx)
        self.assertEqual(json.loads(out.getvalue())["reason"], "context_injection")

    def test_tailscale_resolver_binds_device_identity(self):
        resolver = TailscaleContextResolver({("kou.ishi@gmail.com", "muse"): CLAUDE})
        resolved = resolver({"tailscale_user": "kou.ishi@gmail.com",
                             "tailscale_device": "muse"})
        # Compare fields, not the object: when the real event_queue module is
        # importable the resolver returns its VerifiedEventIdentity class.
        self.assertEqual(
            (resolved.subject, resolved.owner, resolved.session,
             resolved.source, resolved.scopes),
            (CLAUDE.subject, CLAUDE.owner, CLAUDE.session,
             CLAUDE.source, CLAUDE.scopes))
        self.assertIsNone(resolver({"tailscale_user": "kou.ishi@gmail.com",
                                    "tailscale_device": "unknown-node"}))
        self.assertIsNone(resolver({"tailscale_user": "mallory@example.com",
                                    "tailscale_device": "muse"}))


if __name__ == "__main__":
    unittest.main()
