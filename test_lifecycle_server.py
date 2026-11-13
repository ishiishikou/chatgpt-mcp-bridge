"""Offline checks for isolated lifecycle-only server."""
import unittest
from pathlib import Path

class LifecycleOnlyTests(unittest.TestCase):
    def test_only_one_public_tool(self):
        import lifecycle_server
        self.assertEqual(
            [t.name for t in lifecycle_server.mcp._tool_manager.list_tools()],
            ["show_lifecycle_probe"],
        )
        tool=lifecycle_server.mcp._tool_manager.list_tools()[0]
        self.assertEqual(tool.meta["ui"]["resourceUri"],
                         "ui://chatgpt-event-probe/lifecycle-display-v1.html")

    def test_widget_has_no_dispatch_or_outbound_network(self):
        src=Path(__file__).with_name("lifecycle-display-v1.html").read_text()
        for forbidden in ("ui/message", "tools/call", "setTimeout(", "setInterval(",
                          "fetch(", "XMLHttpRequest", "WebSocket", "EventSource"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden,src)

if __name__ == "__main__":
    unittest.main()
