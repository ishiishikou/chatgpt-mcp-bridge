"""Offline mock transport cases; never connects to ChatGPT or MCP."""
from pathlib import Path
import tempfile
import unittest

from response_receipt_adapter import (
    READ_SCOPE, WRITE_SCOPE, ReceiptCallbackAdapter, VerifiedIdentity,
)
from response_receipts import ACK_PHRASE, initialize_receipts, prepare_receipt


class ReceiptAuthAdapterTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="receipt-adapter-test-")
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "receipts.sqlite3"
        initialize_receipts(self.path)
        self.event = "event-1"
        self.principal = "trusted:identity"
        setup = prepare_receipt(self.event, self.principal, path=self.path,
                                enabled=True)
        self.assertTrue(setup["prepared"])
        # This is a server-controlled mapping, not an MCP tool parameter.
        self.contexts = {
            "authenticated": VerifiedIdentity(
                self.principal, frozenset({WRITE_SCOPE, READ_SCOPE})),
            "wrong_owner": VerifiedIdentity(
                "other:identity", frozenset({WRITE_SCOPE, READ_SCOPE})),
            "read_only": VerifiedIdentity(
                self.principal, frozenset({READ_SCOPE})),
            "write_only": VerifiedIdentity(
                self.principal, frozenset({WRITE_SCOPE})),
            "anonymous": None,
        }

        def trusted_resolver(context):
            return self.contexts.get(context)

        self.adapter = ReceiptCallbackAdapter(
            trusted_resolver, path=self.path, enabled=True)
        self.args = {"event_id": self.event, "phrase": ACK_PHRASE}

    def test_default_disabled(self):
        adapter = ReceiptCallbackAdapter(
            lambda context: self.contexts.get(context), path=self.path)
        self.assertEqual(adapter.record("authenticated", self.args)["reason"],
                         "disabled")

    def test_valid_verified_identity_records_once(self):
        self.assertTrue(self.adapter.record("authenticated", self.args)["recorded"])
        self.assertEqual(self.adapter.record("authenticated", self.args)["reason"],
                         "already_received")
        self.assertEqual(self.adapter.status("authenticated", self.event)["status"],
                         "received")

    def test_anonymous_denied(self):
        self.assertEqual(self.adapter.record("anonymous", self.args)["reason"],
                         "not_authorized")
        self.assertEqual(self.adapter.status("anonymous", self.event)["status"],
                         "not_authorized")

    def test_wrong_owner_denied(self):
        self.assertEqual(self.adapter.record("wrong_owner", self.args)["reason"],
                         "not_authorized")

    def test_write_scope_required(self):
        self.assertEqual(self.adapter.record("read_only", self.args)["reason"],
                         "not_authorized")

    def test_read_scope_required(self):
        self.assertEqual(self.adapter.status("write_only", self.event)["status"],
                         "not_authorized")

    def test_model_cannot_override_principal_or_enable(self):
        for illegal in (
            {**self.args, "authenticated_principal": self.principal},
            {**self.args, "enabled": True},
            {**self.args, "path": str(self.path)},
            {**self.args, "now": 1},
        ):
            self.assertEqual(self.adapter.record("anonymous", illegal)["reason"],
                             "invalid_arguments")
        self.assertEqual(self.adapter.status("authenticated", self.event)["status"],
                         "pending")

    def test_malformed_transport_context_denied(self):
        self.contexts["spoofed"] = {"subject": self.principal,
                                     "scopes": {WRITE_SCOPE}}
        self.assertEqual(self.adapter.record("spoofed", self.args)["reason"],
                         "not_authorized")

    def test_broken_auth_resolver_fails_closed(self):
        def broken(_):
            raise RuntimeError("auth provider unavailable")
        adapter = ReceiptCallbackAdapter(broken, path=self.path, enabled=True)
        self.assertEqual(adapter.record("authenticated", self.args)["reason"],
                         "not_authorized")

    def test_no_arbitrary_response_storage(self):
        invalid = {"event_id": self.event, "phrase": "private chat transcript"}
        self.assertEqual(self.adapter.record("authenticated", invalid)["reason"],
                         "unexpected_phrase")


if __name__ == "__main__":
    unittest.main()