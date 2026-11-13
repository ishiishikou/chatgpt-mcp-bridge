"""Integration test against the real PR #7 ``response_receipts`` module.

Skipped when ``response_receipts.py`` is not importable (it lives on the
PR #7 branch until merged). Verifies the Muse client's read-only receipt
path end to end: operator prepares a receipt bound to a principal, the
fixed acknowledgement is recorded through the module's own API (standing in
for the future model-mediated tool call), and the client reads the status
back through the shim, whose receipt function derives the principal from
the resolved transport identity — never from request parameters.

    python -m unittest claude_client.test_receipt_integration -v
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
    import response_receipts
except ImportError:
    response_receipts = None

if response_receipts is not None:
    from claude_client.client import EventClient, LocalTransport, Outbox
    from claude_client.identity import TokenRegistryResolver
    from claude_client.shim import BridgeShim

    SCOPES = ["bridge:event:authorize", "bridge:event:enqueue",
              "bridge:event:claim", "bridge:event:report",
              "bridge:event:cancel", "bridge:event:read"]
    TOKEN = "receipt-integration-token"
    DIGEST = hashlib.sha256(TOKEN.encode()).hexdigest()
    PRINCIPAL = "muse-vm"  # == identity.subject provisioned below


@unittest.skipIf(response_receipts is None,
                 "response_receipts module (PR #7) not present")
class RealReceiptIntegrationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        os.chmod(base, 0o700)
        registry = base / "registry.json"
        registry.write_text(json.dumps({DIGEST: {
            "subject": PRINCIPAL, "owner": "user", "session": "s-1",
            "source": "muse", "scopes": SCOPES}}), encoding="utf-8")
        os.chmod(registry, 0o600)
        self.resolver = TokenRegistryResolver(registry)
        state = base / "state"
        state.mkdir(mode=0o700)
        self.receipt_path = state / "response_receipts.sqlite3"
        response_receipts.initialize_receipts(self.receipt_path)

        def receipt_status(context, event_id):
            identity = self.resolver(context)
            if identity is None:
                return {"status": "not_authorized"}
            return response_receipts.get_receipt_status(
                event_id, authenticated_principal=identity.subject,
                path=self.receipt_path)

        class _NoQueue:
            pass

        self.shim = BridgeShim(_NoQueue(), self.resolver,
                               receipt_status_fn=receipt_status)
        self.ctx = {"presented_token": TOKEN}
        self.client = EventClient(
            LocalTransport(self.shim, self.ctx),
            Outbox(base / "outbox" / "outbox.sqlite3"))

    def test_receipt_round_trip_and_principal_binding(self):
        event_id = self.client.prepare("ack check")
        prepared = response_receipts.prepare_receipt(
            event_id, PRINCIPAL, enabled=True, ttl_seconds=300,
            path=self.receipt_path)
        self.assertTrue(prepared["prepared"], prepared)
        self.assertEqual(self.client.receipt(event_id)["receipt_status"], "pending")
        recorded = response_receipts.record_receipt(
            event_id, response_receipts.ACK_PHRASE,
            authenticated_principal=PRINCIPAL, enabled=True,
            path=self.receipt_path)
        self.assertTrue(recorded["recorded"], recorded)
        self.assertEqual(self.client.receipt(event_id)["receipt_status"], "received")
        # A different principal must not be able to read or record it.
        other = response_receipts.get_receipt_status(
            event_id, authenticated_principal="someone-else",
            path=self.receipt_path)
        self.assertEqual(other["status"], "not_authorized")


if __name__ == "__main__":
    unittest.main()
