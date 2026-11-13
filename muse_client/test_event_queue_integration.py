"""Integration test against the real PR #9 ``event_queue`` module.

Skipped automatically when ``event_queue.py`` is not importable (it lives on
the PR #9 branch until merged). When present, this verifies that the Muse
client's resolvers produce identities the real OfflineEventQueue accepts
(its ``isinstance`` check), and that the full authorize -> enqueue -> claim ->
report flow works against the real SQLite ledger in a temp directory.

    python -m unittest muse_client.test_event_queue_integration -v
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import event_queue
except ImportError:
    event_queue = None

if event_queue is not None:
    from muse_client.client import EventClient, LocalTransport, Outbox
    from muse_client.identity import Identity, TokenRegistryResolver
    from muse_client.shim import BridgeShim

    SCOPES = frozenset({
        "bridge:event:authorize", "bridge:event:enqueue", "bridge:event:claim",
        "bridge:event:report", "bridge:event:cancel", "bridge:event:read",
    })
    TOKEN = "integration-test-token"
    DIGEST = hashlib.sha256(TOKEN.encode()).hexdigest()


@unittest.skipIf(event_queue is None, "event_queue module (PR #9) not present")
class RealQueueIntegrationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        os.chmod(base, 0o700)
        registry = base / "registry.json"
        registry.write_text(json.dumps({DIGEST: {
            "subject": "muse-vm", "owner": "user", "session": "s-1",
            "source": "muse", "scopes": sorted(SCOPES)}}), encoding="utf-8")
        os.chmod(registry, 0o600)
        self.resolver = TokenRegistryResolver(registry)
        state = base / "state"
        state.mkdir(mode=0o700)
        self.queue_path = state / "event_queue.sqlite3"
        event_queue.initialize_queue(self.queue_path)
        self.queue = event_queue.OfflineEventQueue(
            self.resolver, path=self.queue_path, enabled=True)
        self.shim = BridgeShim(self.queue, self.resolver)
        self.ctx = {"presented_token": TOKEN}
        self.client = EventClient(
            LocalTransport(self.shim, self.ctx),
            Outbox(base / "outbox" / "outbox.sqlite3"))

    def test_full_flow_against_real_queue(self):
        event_id = self.client.prepare("integration check")
        authorized = self.queue.authorize(self.ctx, event_id, "muse", ttl_seconds=300)
        self.assertTrue(authorized["authorized"], authorized)
        self.assertEqual(self.client.send(event_id), {"sent": True, "state": "queued"})
        self.assertEqual(self.client.status(event_id)["queue_status"], "queued")
        claimed = self.queue.claim(self.ctx, event_id)
        self.assertTrue(claimed["claimed"], claimed)
        self.assertEqual(claimed["message"], "integration check")
        self.assertFalse(self.queue.claim(self.ctx, event_id)["claimed"])
        reported = self.queue.report(self.ctx, event_id, claimed["claim_token"],
                                     "host_accepted")
        self.assertTrue(reported["recorded"], reported)
        self.assertEqual(self.client.status(event_id)["queue_status"], "host_accepted")

    def test_forged_source_cannot_enqueue(self):
        # A resolver for source=codex must not enqueue an event authorized
        # for source=muse (owner/session/source binding in the real queue).
        codex_registry = Path(self._tmp.name) / "registry2.json"
        codex_registry.write_text(json.dumps({DIGEST: {
            "subject": "codex", "owner": "user", "session": "s-1",
            "source": "codex", "scopes": sorted(SCOPES)}}), encoding="utf-8")
        os.chmod(codex_registry, 0o600)
        codex_resolver = TokenRegistryResolver(codex_registry)
        codex_queue = event_queue.OfflineEventQueue(
            codex_resolver, path=self.queue_path, enabled=True)
        event_id = self.client.prepare("bound to muse")
        self.assertTrue(self.queue.authorize(self.ctx, event_id, "muse")["authorized"])
        result = codex_queue.enqueue(self.ctx, event_id, "bound to muse")
        self.assertEqual(result, {"queued": False, "reason": "not_authorized"})


if __name__ == "__main__":
    unittest.main()
