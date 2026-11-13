"""Offline contract tests for a distinct per-thread MCP Apps entrypoint."""
import unittest
from pathlib import Path
from unittest.mock import patch

import thread_entrypoint_server as source

class ThreadEntrypointContractTests(unittest.TestCase):
    def test_exactly_one_tool(self):
        items=source.mcp._tool_manager.list_tools()
        self.assertEqual([x.name for x in items],["show_thread_entrypoint_probe"])
        self.assertEqual(source.show_thread_entrypoint_probe(None),
                         "Thread entrypoint v1: display-only, no message sending or callbacks.")

    def test_thread_entrypoint_metadata(self):
        tool=source.mcp._tool_manager.list_tools()[0]
        self.assertEqual(tool.meta["openai/ui"],{"entrypoints":[{"type":"thread"}]})
        self.assertEqual(tool.meta["ui"]["resourceUri"],source.URI)
        self.assertEqual(source.URI,"ui://chatgpt-mcp-bridge/thread-entrypoint-v1.html")

    def test_html_does_not_send_or_reach_network(self):
        html=Path(source.__file__).with_name("thread-entrypoint-v1.html").read_text()
        self.assertIn("ui/initialize",html)
        self.assertIn("ui/notifications/initialized",html)
        for invalid in ("ui/message","tools/call","ui/update-model-context",
                        "fetch(","XMLHttpRequest","WebSocket","EventSource",
                        "setTimeout(","setInterval(","http://","https://"):
            with self.subTest(invalid=invalid):
                self.assertNotIn(invalid,html)

    def test_no_bridge_event_queue_or_dispatch_imports(self):
        code=Path(source.__file__).read_text()
        for keyword in ("dispatch_store","event_queue","response_receipts",
                        "MCP_AUTO_SEND_ENABLED","claim_once","claim_guarded_probe"):
            with self.subTest(keyword=keyword):
                self.assertNotIn(keyword,code)

    def test_no_runtime_state_files_created_by_import(self):
        import importlib
        with patch("pathlib.Path.mkdir",side_effect=AssertionError("write")):
            importlib.reload(source)

if __name__ == "__main__":
    unittest.main()
