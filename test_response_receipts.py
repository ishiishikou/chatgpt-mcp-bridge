"""Offline-only tests; never call ChatGPT, MCP, tunnel or network."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from response_receipts import (
    ACK_PHRASE, get_receipt_status, initialize_receipts, prepare_receipt, record_receipt,
)


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="receipt-unit-")
        self.addCleanup(t.cleanup)
        self.path = Path(t.name) / "db.sqlite3"
        self.event = "e-test-1"
        self.owner = "principal:demo"

    def prepare(self, **kw):
        return prepare_receipt(self.event, self.owner, path=self.path,
                               enabled=True, now=1000, **kw)

    def receive(self, **kw):
        return record_receipt(self.event, ACK_PHRASE, path=self.path,
                              enabled=True, authenticated_principal=self.owner,
                              now=1001, **kw)

    def test_default_disabled_never_creates_db(self):
        self.assertEqual(prepare_receipt(self.event,self.owner,path=self.path)["reason"],
                         "disabled")
        self.assertEqual(record_receipt(self.event,ACK_PHRASE,path=self.path)["reason"],
                         "disabled")
        self.assertFalse(self.path.exists())

    def test_missing_db_fails_closed(self):
        self.assertEqual(self.prepare()["reason"], "ledger_unavailable")
        self.assertEqual(self.receive()["reason"], "ledger_unavailable")
        self.assertFalse(self.path.exists())

    def test_fixed_phrase_happy_path_and_restart(self):
        initialize_receipts(self.path)
        self.assertTrue(self.prepare()["prepared"])
        self.assertTrue(self.receive()["recorded"])
        self.assertEqual(get_receipt_status(self.event,path=self.path,
                                            authenticated_principal=self.owner)["status"],
                         "received")
        initialize_receipts(self.path)
        self.assertEqual(self.receive()["reason"], "already_received")

    def test_wrong_principal_rejected_even_if_phrase_correct(self):
        initialize_receipts(self.path)
        self.prepare()
        a=record_receipt(self.event,ACK_PHRASE,path=self.path,enabled=True,
                         authenticated_principal="principal:attacker",now=1001)
        self.assertEqual(a["reason"],"not_authorized")
        self.assertEqual(get_receipt_status(self.event,path=self.path,
                                            authenticated_principal="principal:attacker")["status"],
                         "not_authorized")
        self.assertEqual(get_receipt_status(self.event,path=self.path,
                                            authenticated_principal=self.owner)["status"],
                         "pending")

    def test_missing_authenticated_principal_rejected(self):
        initialize_receipts(self.path)
        self.prepare()
        self.assertEqual(record_receipt(self.event,ACK_PHRASE,path=self.path,
                                        enabled=True,now=1001)["reason"],"not_authorized")

    def test_unexpected_phrase_rejected(self):
        initialize_receipts(self.path)
        self.prepare()
        self.assertEqual(record_receipt(self.event,"send conversation history",path=self.path,
                                        enabled=True,authenticated_principal=self.owner,
                                        now=1001)["reason"],"unexpected_phrase")
        self.assertEqual(get_receipt_status(self.event,path=self.path,
                                            authenticated_principal=self.owner)["status"],
                         "pending")

    def test_expiry_rejected(self):
        initialize_receipts(self.path)
        self.prepare(ttl_seconds=1)
        self.assertEqual(record_receipt(self.event,ACK_PHRASE,path=self.path,
                                        enabled=True,authenticated_principal=self.owner,
                                        now=1002)["reason"],"expired")

    def test_duplicate_event_and_global_limit(self):
        initialize_receipts(self.path)
        self.prepare()
        self.assertEqual(self.prepare()["reason"],"already_prepared")
        self.assertEqual(prepare_receipt("other",self.owner,path=self.path,
                                         enabled=True,now=1000)["reason"],"global_limit")

    def test_no_unprepared_event(self):
        initialize_receipts(self.path)
        self.assertEqual(self.receive()["reason"],"not_authorized")

    def test_parallel_one_receipt_only(self):
        initialize_receipts(self.path)
        self.prepare()
        with ThreadPoolExecutor(max_workers=6) as pool:
            replies = list(pool.map(lambda _:self.receive(), range(6)))
        self.assertEqual(sum(x["recorded"] for x in replies),1)
        self.assertEqual(sum(x.get("reason")=="already_received" for x in replies),5)

    def test_bad_identifiers_and_ttl(self):
        initialize_receipts(self.path)
        self.assertEqual(prepare_receipt("../../../file",self.owner,path=self.path,enabled=True,
                                         now=1000)["reason"],"invalid_identifier")
        self.assertEqual(self.prepare(ttl_seconds=3601)["reason"],"invalid_ttl")

    def test_private_db_permissions_and_insecure_parent(self):
        initialize_receipts(self.path)
        self.assertEqual(self.path.stat().st_mode & 0o077,0)
        self.assertEqual(self.path.parent.stat().st_mode & 0o077,0)
        unsafe = self.path.parent / "unsafe"
        unsafe.mkdir(mode=0o700)
        unsafe.chmod(0o777)
        with self.assertRaises(PermissionError):
            initialize_receipts(unsafe / "bad.sqlite3")
        self.assertFalse((unsafe / "bad.sqlite3").exists())

    def test_symlink_database_rejected(self):
        link = self.path.parent / "link.sqlite3"
        link.symlink_to(self.path)
        with self.assertRaises(RuntimeError):
            initialize_receipts(link)
        self.assertFalse(self.path.exists())

    def test_invalid_test_clock_is_rejected(self):
        initialize_receipts(self.path)
        self.assertEqual(prepare_receipt(self.event,self.owner,enabled=True,
                                         path=self.path,now="bad")["reason"],"invalid_clock")
        self.assertEqual(record_receipt(self.event,ACK_PHRASE,enabled=True,
                                        authenticated_principal=self.owner,
                                        path=self.path,now="bad")["reason"],"invalid_clock")


if __name__=="__main__":
    unittest.main()