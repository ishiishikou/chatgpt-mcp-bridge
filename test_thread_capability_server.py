"""Contract tests for isolated capability-only Thread Entrypoint."""
from contextlib import ExitStack
import importlib
from pathlib import Path
import unittest
from unittest.mock import patch

import thread_capability_server as source


class CapabilityOnlyContractTests(unittest.TestCase):
    def test_one_display_only_thread_tool(self):
        tools = source.mcp._tool_manager.list_tools()
        self.assertEqual([t.name for t in tools], ["show_thread_capability_probe"])
        self.assertEqual(tools[0].meta["openai/ui"], {"entrypoints": [{"type": "thread"}]})
        self.assertEqual(tools[0].meta["ui"]["resourceUri"], source.URI)
        self.assertEqual(source.BUILD, "capdiag-20261114-v1")
        self.assertIn(source.BUILD, source.show_thread_capability_probe(None))

    def test_new_versioned_resource_without_sender(self):
        self.assertEqual(source.URI, "ui://chatgpt-mcp-bridge/thread-capabilities-20261114-v1.html")
        html = Path(source.__file__).with_name("thread-capabilities-20261114-v1.html").read_text()
        self.assertIn(source.BUILD, html)
        self.assertIn("ui/initialize", html)
        self.assertIn("ui/notifications/initialized", html)
        for forbidden in ("ui/message", "tools/call", "ui/update-model-context",
                          "fetch(", "XMLHttpRequest", "WebSocket", "EventSource",
                          "setTimeout(", "setInterval(", "http://", "https://"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, html)

    def test_diagnostic_cannot_import_sender_code(self):
        code = Path(source.__file__).read_text()
        for forbidden in ("dispatch_store", "event_queue", "response_receipts",
                          "claim_once", "claim_guarded_probe", "MCP_AUTO_SEND_ENABLED"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, code)

    def test_import_is_read_only(self):
        """Patch write interfaces before a *fresh* import, not only a module reload."""
        import subprocess
        import sys
        code = r"""
from contextlib import ExitStack
from unittest.mock import patch
with ExitStack() as stack:
    for call in ("pathlib.Path.mkdir", "pathlib.Path.touch",
                 "pathlib.Path.write_text", "pathlib.Path.write_bytes",
                 "sqlite3.connect"):
        stack.enter_context(patch(call, side_effect=AssertionError(call)))
    import thread_capability_server
"""
        finished = subprocess.run(
            [sys.executable, "-B", "-c", code],
            cwd=Path(source.__file__).parent,
            capture_output=True, text=True, timeout=20, check=False,
        )
        self.assertEqual(finished.returncode, 0, finished.stderr)

    def test_original_sender_stays_fail_closed_for_every_flag(self):
        import json
        import os
        import server

        for flag in (None, "0", "1"):
            env = {} if flag is None else {"MCP_AUTO_SEND_ENABLED": flag}
            with self.subTest(flag=flag), patch.dict(
                os.environ, env, clear=False
            ), patch("sqlite3.connect", side_effect=AssertionError("ledger access")):
                if flag is None:
                    with patch.dict(os.environ):
                        os.environ.pop("MCP_AUTO_SEND_ENABLED", None)
                        result = json.loads(server.claim_guarded_probe(None))
                else:
                    result = json.loads(server.claim_guarded_probe(None))
                self.assertFalse(result["granted"])
                self.assertEqual(result["reason"], "target_unverified" if flag == "1" else "disabled")


if __name__ == "__main__":
    unittest.main()