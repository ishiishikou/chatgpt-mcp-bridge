import json
import re
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dispatch_store import (
    claim_once, get_status, initialize_database, report_result, MAX_CLAIMS,
)


class DispatchStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "dispatch.sqlite3"

    def tearDown(self):
        self.directory.cleanup()

    def claim(self, event_id):
        return claim_once(event_id, enabled=True, path=self.path)

    def test_disabled_and_missing_ledger_fail_closed(self):
        self.assertEqual(claim_once("first", path=self.path)["reason"], "disabled")
        self.assertFalse(self.path.exists())
        self.assertEqual(self.claim("first")["reason"], "ledger_unavailable")
        self.assertFalse(self.path.exists())

    def test_concurrent_one_only(self):
        initialize_database(self.path)
        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(self.claim, ["same-id"] * 30))
        self.assertEqual(sum(x["granted"] for x in results), 1)
        self.assertEqual(get_status("same-id", path=self.path)["status"], "claimed")
        self.assertEqual(self.claim("same-id")["reason"], "already_claimed")

    def test_outcome_unknown_never_reopens_claim(self):
        initialize_database(self.path)
        token = self.claim("x")["claim_token"]
        self.assertFalse(report_result("x", "wrong", "host_accepted", path=self.path)["recorded"])
        self.assertTrue(report_result("x", token, "unknown", path=self.path)["recorded"])
        self.assertTrue(report_result("x", token, "unknown", path=self.path)["idempotent"])
        self.assertEqual(report_result("x", token, "host_accepted", path=self.path)["reason"],
                         "already_finalized")
        self.assertEqual(self.claim("x")["reason"], "already_claimed")

    def test_acceptance_is_only_host_level(self):
        initialize_database(self.path)
        token = self.claim("x")["claim_token"]
        self.assertTrue(report_result("x", token, "host_accepted", path=self.path)["recorded"])
        self.assertEqual(get_status("x", path=self.path)["status"], "host_accepted")

    def test_global_claim_limit_and_restart_persistence(self):
        initialize_database(self.path)
        for i in range(MAX_CLAIMS):
            self.assertTrue(self.claim(f"event-{i}")["granted"])
        self.assertEqual(self.claim("excess")["reason"], "global_limit")
        initialize_database(self.path)  # simulates reinitialization without clearing state
        self.assertEqual(self.claim("event-0")["reason"], "already_claimed")
        self.assertEqual(get_status("event-0", path=self.path)["status"], "claimed")

    def test_ledger_deletion_fails_closed(self):
        initialize_database(self.path)
        self.assertTrue(self.claim("x")["granted"])
        self.path.unlink()
        self.assertEqual(self.claim("x")["reason"], "ledger_unavailable")
        self.assertFalse(self.path.exists())

    def test_legacy_widget_is_inert(self):
        legacy = Path(__file__).with_name("auto-probe.html").read_text()
        self.assertNotIn("ui/message", legacy)
        self.assertNotIn("setTimeout(", legacy)

    def test_server_default_is_off(self):
        import os
        import server
        previous = os.environ.pop("MCP_AUTO_SEND_ENABLED", None)
        try:
            result = json.loads(server.claim_guarded_probe(None))
            self.assertEqual(result["reason"], "disabled")
            self.assertFalse(result["granted"])
        finally:
            if previous is not None:
                os.environ["MCP_AUTO_SEND_ENABLED"] = previous


    def test_server_env_flag_cannot_authorize_unbound_recipient(self):
        import os
        import server
        from unittest.mock import patch
        for flag, expected in (("0", "disabled"), ("1", "target_unverified")):
            with self.subTest(flag=flag):
                with patch.dict(os.environ, {"MCP_AUTO_SEND_ENABLED": flag}):
                    with patch("dispatch_store.claim_once", side_effect=AssertionError("should not claim")):
                        with patch("sqlite3.connect", side_effect=AssertionError("should not touch DB")):
                            outcome = json.loads(server.claim_guarded_probe(None))
                self.assertEqual(outcome, {"granted": False, "reason": expected})

    def test_internal_tools_have_no_ui_resource(self):
        import server
        tools = {tool.name: tool for tool in server.mcp._tool_manager.list_tools()}
        for name in ("claim_guarded_probe", "report_guarded_probe", "guarded_probe_status"):
            self.assertEqual(tools[name].meta, {"ui": {"visibility": ["app"]}})
        self.assertEqual(tools["show_auto_probe"].meta["ui"]["resourceUri"],
                         "ui://chatgpt-event-probe/auto.html")

    def test_lifecycle_widget_is_separate_and_display_only(self):
        import server
        tools = {tool.name: tool for tool in server.mcp._tool_manager.list_tools()}
        self.assertEqual(tools["show_lifecycle_probe"].meta["ui"]["resourceUri"],
                         "ui://chatgpt-event-probe/lifecycle-display-v1.html")
        source = Path(__file__).with_name("lifecycle-display-v1.html").read_text()
        for forbidden in ("ui/message", "tools/call", "setTimeout", "setInterval", "fetch("):
            self.assertNotIn(forbidden, source)

    def test_cross_process_concurrent_claims_are_atomic(self):
        initialize_database(self.path)
        code = ("import json,sys; from pathlib import Path; "
                "from dispatch_store import claim_once; "
                "print(json.dumps(claim_once('shared',enabled=True,path=Path(sys.argv[1]))))")
        def child(_):
            run = subprocess.run(
                [sys.executable, "-c", code, str(self.path)],
                cwd=Path(__file__).parent, capture_output=True, text=True, check=True
            )
            return json.loads(run.stdout)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(child, range(12)))
        self.assertEqual(sum(x["granted"] for x in results), 1)
        self.assertEqual(get_status("shared", path=self.path)["status"], "claimed")

    def test_real_interpreter_restart_does_not_reclaim(self):
        initialize_database(self.path)
        self.assertTrue(self.claim("real-restart")["granted"])
        code = ("import json,sys; from pathlib import Path; "
                "from dispatch_store import claim_once; "
                "print(json.dumps(claim_once('real-restart',enabled=True,path=Path(sys.argv[1]))))")
        result = subprocess.run(
            [sys.executable, "-c", code, str(self.path)],
            cwd=Path(__file__).parent, capture_output=True, text=True, check=True
        )
        self.assertEqual(json.loads(result.stdout)["reason"], "already_claimed")

    def test_html_embedded_javascript_parses_offline(self):
        for filename in ("guarded-probe.html", "auto-probe.html"):
            html = Path(__file__).with_name(filename).read_text()
            match = re.search(r"<script>(.*?)</script>", html, re.DOTALL)
            if not match:
                self.assertEqual(filename, "auto-probe.html")
                continue
            result = subprocess.run(
                ["node", "--check", "--input-type=commonjs"],
                input=match.group(1), text=True, capture_output=True
            )
            self.assertEqual(result.returncode, 0,
                             f"{filename} syntax error: {result.stderr}")


if __name__ == "__main__":
    unittest.main()
