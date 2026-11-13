"""Offline contract tests: no MCP, browser, network, or live dispatch."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import os
import sqlite3
import unittest

from event_queue import (
    DEFAULT_DB, MAX_MESSAGE_BYTES, OfflineEventQueue, VerifiedEventIdentity,
    initialize_queue, _existing,
)


class _BumpClockAfterBegin:
    """Deterministically simulate time passing while BEGIN IMMEDIATE is blocked."""

    def __init__(self, real_connection, clock, timestamp):
        self.db, self.clock, self.timestamp = real_connection, clock, timestamp

    def execute(self, statement, *args):
        cursor = self.db.execute(statement, *args)
        if statement.strip().upper() == "BEGIN IMMEDIATE":
            self.clock.return_value = self.timestamp
        return cursor

    def __enter__(self):
        self.db.__enter__()
        return self

    def __exit__(self, *args):
        return self.db.__exit__(*args)

    def close(self):
        self.db.close()


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "private" / "queue.sqlite3"
        initialize_queue(self.path)
        self.clock = patch("event_queue.time.time", return_value=10000)
        self.mock_time = self.clock.start()
        self.addCleanup(self.clock.stop)
        self.ctx = {
            "operator": VerifiedEventIdentity(
                "operator", "owner1", "chat1", "operator",
                frozenset({"bridge:event:authorize", "bridge:event:cancel", "bridge:event:read"})),
            "muse": VerifiedEventIdentity(
                "muse-service", "owner1", "chat1", "muse",
                frozenset({"bridge:event:enqueue", "bridge:event:read"})),
            "codex": VerifiedEventIdentity(
                "codex-service", "owner1", "chat1", "codex",
                frozenset({"bridge:event:enqueue"})),
            "host": VerifiedEventIdentity(
                "chatgpt-app", "owner1", "chat1", "chatgpt",
                frozenset({"bridge:event:claim", "bridge:event:report", "bridge:event:read"})),
            "other": VerifiedEventIdentity(
                "attacker", "other_owner", "chat1", "muse",
                frozenset({"bridge:event:authorize", "bridge:event:enqueue",
                           "bridge:event:claim", "bridge:event:read", "bridge:event:cancel",
                           "bridge:event:report"})),
            "other_chat": VerifiedEventIdentity(
                "other-chat", "owner1", "chat2", "muse",
                frozenset({"bridge:event:enqueue", "bridge:event:claim", "bridge:event:read"})),
        }
        self.q = OfflineEventQueue(lambda c: self.ctx.get(c), enabled=True, path=self.path)

    def authorize(self, **kwargs):
        return self.q.authorize("operator", "event1", "muse", **kwargs)

    def queue(self):
        self.assertTrue(self.authorize()["authorized"])
        self.assertTrue(self.q.enqueue("muse", "event1", "test event")["queued"])

    def test_disabled_by_default(self):
        q = OfflineEventQueue(lambda c: self.ctx.get(c), path=self.path)
        self.assertEqual(q.authorize("operator", "event1", "muse")["reason"], "not_authorized")
        self.assertEqual(q.enqueue("muse", "event1", "hello")["reason"], "not_authorized")
        self.assertEqual(q.claim("host", "event1")["reason"], "not_authorized")
        self.assertEqual(q.status("host", "event1")["status"], "not_authorized")

    def test_missing_db_fails_closed(self):
        q = OfflineEventQueue(lambda c: self.ctx.get(c), enabled=True,
                              path=self.path.parent / "missing.sqlite3")
        self.assertEqual(q.authorize("operator", "event1", "muse")["reason"], "ledger_unavailable")
        self.assertFalse((self.path.parent / "missing.sqlite3").exists())

    def test_symlink_db_refused(self):
        link = self.path.parent / "link.sqlite3"
        link.symlink_to(self.path)
        with self.assertRaises(RuntimeError):
            initialize_queue(link)
        q = OfflineEventQueue(lambda c: self.ctx.get(c), path=link, enabled=True)
        self.assertEqual(q.authorize("operator", "event1", "muse")["reason"], "ledger_unavailable")

    def test_private_permissions_fail_closed(self):
        os.chmod(self.path, 0o644)
        self.assertEqual(self.authorize()["reason"], "ledger_unavailable")
        with self.assertRaises(PermissionError):
            initialize_queue(self.path)

    def test_identity_resolution_errors_deny(self):
        q = OfflineEventQueue(lambda c: 1 / 0, enabled=True, path=self.path)
        self.assertEqual(q.authorize("whatever", "event1", "muse")["reason"], "not_authorized")
        self.assertEqual(self.q.authorize("missing", "event1", "muse")["reason"], "not_authorized")
        self.assertEqual(self.q.authorize("muse", "event1", "muse")["reason"], "not_authorized")

    def test_authorization_precedes_event_ingest(self):
        self.assertEqual(self.q.enqueue("muse", "event1", "test")["reason"], "not_authorized")
        self.assertEqual(self.q.claim("host", "event1")["reason"], "not_authorized")
        self.assertEqual(self.q.status("host", "event1")["status"], "not_authorized")

    def test_no_source_spoofing(self):
        self.assertTrue(self.authorize()["authorized"])
        self.assertEqual(self.q.enqueue("codex", "event1", "test")["reason"], "not_authorized")
        self.assertEqual(self.q.enqueue("other", "event1", "test")["reason"], "not_authorized")
        self.assertEqual(self.q.enqueue("other_chat", "event1", "test")["reason"], "not_authorized")
        self.assertEqual(self.q.authorize("operator", "evt2", "not-allowed")["reason"],
                         "invalid_arguments")

    def test_invalid_ids_ttls_and_messages(self):
        self.assertEqual(self.q.authorize("operator", "💣", "muse")["reason"], "invalid_arguments")
        self.assertEqual(self.q.authorize("operator", "event1", "muse", ttl_seconds=True)["reason"],
                         "invalid_arguments")
        self.assertEqual(self.q.authorize("operator", "event1", "muse", ttl_seconds=301)["reason"],
                         "invalid_arguments")
        self.assertTrue(self.authorize()["authorized"])
        for message in ("  ", "bad\x00byte", "あ" * (MAX_MESSAGE_BYTES // 3 + 1), 1234):
            self.assertEqual(self.q.enqueue("muse", "event1", message)["reason"],
                             "invalid_arguments")
        self.assertTrue(self.q.enqueue("muse", "event1", "あ" * 256)["queued"])

    def test_duplicate_event_and_permanent_global_limit(self):
        self.assertTrue(self.authorize()["authorized"])
        self.assertEqual(self.authorize()["reason"], "duplicate")
        self.assertEqual(self.q.authorize("operator", "event2", "codex")["reason"], "global_limit")
        self.assertTrue(self.q.cancel("operator", "event1")["cancelled"])
        self.assertEqual(self.q.authorize("operator", "event2", "codex")["reason"], "global_limit")

    def test_expired_events_never_claimed(self):
        self.authorize(ttl_seconds=20)
        self.mock_time.return_value = 10020
        self.assertEqual(self.q.enqueue("muse", "event1", "x")["reason"], "expired")
        self.assertEqual(self.q.status("host", "event1")["status"], "expired")

    def test_enqueue_checks_expiry_after_write_lock(self):
        self.authorize(ttl_seconds=10)
        self.mock_time.return_value = 10009
        with patch("event_queue._existing", side_effect=lambda p:
                   _BumpClockAfterBegin(_existing(p), self.mock_time, 10010)):
            self.assertEqual(self.q.enqueue("muse", "event1", "late")["reason"],
                             "expired")
        self.assertEqual(self.q.status("host", "event1")["status"], "expired")

    def test_claim_checks_expiry_after_write_lock(self):
        self.authorize(ttl_seconds=10)
        self.assertTrue(self.q.enqueue("muse", "event1", "safe")["queued"])
        self.mock_time.return_value = 10009
        with patch("event_queue._existing", side_effect=lambda p:
                   _BumpClockAfterBegin(_existing(p), self.mock_time, 10010)):
            self.assertEqual(self.q.claim("host", "event1")["reason"], "expired")
        self.assertEqual(self.q.status("host", "event1")["status"], "expired")

    def test_claim_returns_exact_one_message_and_token(self):
        self.queue()
        result = self.q.claim("host", "event1")
        self.assertTrue(result["claimed"])
        self.assertEqual(result["message"], "test event")
        self.assertGreaterEqual(len(result["claim_token"]), 32)
        self.assertEqual(self.q.claim("host", "event1")["reason"], "not_queued")
        self.assertEqual(self.q.enqueue("muse", "event1", "retry")["reason"], "already_used")
        self.assertEqual(self.q.status("host", "event1"), {"status": "claimed"})

    def test_claim_wrong_owner_or_session_denied(self):
        self.queue()
        self.assertEqual(self.q.claim("other", "event1")["reason"], "not_authorized")
        self.assertEqual(self.q.claim("other_chat", "event1")["reason"], "not_authorized")
        self.assertTrue(self.q.claim("host", "event1")["claimed"])

    def test_concurrent_claim_only_one(self):
        self.queue()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.q.claim("host", "event1"), range(12)))
        self.assertEqual(sum(1 for x in results if x["claimed"]), 1)

    def test_host_report_not_assistant_receipt(self):
        self.queue()
        token = self.q.claim("host", "event1")["claim_token"]
        self.assertEqual(self.q.report("host", "event1", "wrong", "host_accepted")["reason"],
                         "invalid_arguments")
        self.assertEqual(self.q.report("host", "event1", "a" * 40, "host_accepted")["reason"],
                         "not_authorized")
        self.assertTrue(self.q.report("host", "event1", token, "unknown")["recorded"])
        repeat = self.q.report("host", "event1", token, "unknown")
        self.assertTrue(repeat["idempotent"])
        self.assertEqual(self.q.report("host", "event1", token, "host_accepted")["reason"],
                         "already_finalized")
        self.assertEqual(self.q.claim("host", "event1")["reason"], "not_queued")
        self.assertEqual(self.q.status("host", "event1")["status"], "unknown")

    def test_cancel_before_claim_only(self):
        self.queue()
        self.assertEqual(self.q.cancel("other", "event1")["reason"], "not_authorized")
        self.assertTrue(self.q.cancel("operator", "event1")["cancelled"])
        self.assertEqual(self.q.claim("host", "event1")["reason"], "not_queued")
        self.assertEqual(self.q.cancel("operator", "event1")["reason"], "too_late_or_finalized")

    def test_cancel_after_claim_cannot_undo_send(self):
        self.queue()
        self.q.claim("host", "event1")
        self.assertEqual(self.q.cancel("operator", "event1")["reason"], "too_late_or_finalized")

    def test_reopen_persists_no_reissue(self):
        self.queue()
        self.q.claim("host", "event1")
        other = OfflineEventQueue(lambda c: self.ctx.get(c), enabled=True, path=self.path)
        self.assertEqual(other.claim("host", "event1")["reason"], "not_queued")
        self.assertEqual(other.authorize("operator", "event2", "muse")["reason"], "global_limit")

    def test_authenticated_session_scoped_status_no_payload(self):
        self.queue()
        self.assertEqual(self.q.status("other", "event1")["status"], "not_authorized")
        self.assertEqual(self.q.status("other_chat", "event1")["status"], "not_authorized")
        self.assertEqual(self.q.status("muse", "event1"), {"status": "queued"})
        self.assertNotIn("message", self.q.status("muse", "event1"))
        self.assertNotIn("claim_token", self.q.status("host", "event1"))

    def test_no_untrusted_clock_or_enabled_override_in_calls(self):
        self.assertTrue(self.authorize()["authorized"])
        with self.assertRaises(TypeError):
            self.q.claim("host", "event1", enabled=True)
        with self.assertRaises(TypeError):
            self.q.enqueue("muse", "event1", "payload", authenticated_principal="x")

    def test_unhashable_source_is_rejected_without_crash(self):
        for source in ([], {}, 123, None):
            self.assertEqual(self.q.authorize("operator", "event1", source)["reason"],
                             "invalid_arguments")
        self.assertTrue(self.authorize()["authorized"])

    def test_invalid_unicode_and_outcome_rejected_without_crash(self):
        self.assertTrue(self.authorize()["authorized"])
        self.assertEqual(self.q.enqueue("muse", "event1", "\ud800")["reason"],
                         "invalid_arguments")
        self.assertTrue(self.q.enqueue("muse", "event1", "safe")["queued"])
        token = self.q.claim("host", "event1")["claim_token"]
        self.assertEqual(self.q.report("host", "event1", token, [])["reason"],
                         "invalid_arguments")
        self.assertEqual(self.q.report("host", "event1", "\u3042" * 40, "unknown")["reason"],
                         "invalid_arguments")
        self.assertTrue(self.q.report("host", "event1", token, "host_accepted")["recorded"])


if __name__ == "__main__":
    unittest.main()
