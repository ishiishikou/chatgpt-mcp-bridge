"""Isolated MCP Apps *thread entrypoint* experiment (Issue #4).

No dispatch, event-queue integration, message API, or resource with sender code.
Entrypoint is only an experiment: a per-thread UI is NOT a server-attested chat ID.
"""
import os
from pathlib import Path
from mcp.server.apps import Apps
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context

URI = "ui://chatgpt-mcp-bridge/thread-entrypoint-v1.html"
HTML = Path(__file__).with_name("thread-entrypoint-v1.html").read_text(encoding="utf-8")

apps = Apps()

@apps.tool(
    resource_uri=URI,
    meta={"openai/ui": {"entrypoints": [{"type": "thread"}]}},
    description=(
        "Open a per-thread, strictly display-only MCP Apps lifecycle probe. "
        "It cannot send messages, claim events, or validate a conversation ID."
    ),
)
def show_thread_entrypoint_probe(ctx: Context) -> str:
    return "Thread entrypoint v1: display-only, no message sending or callbacks."

apps.add_html_resource(URI, HTML, title="Thread entrypoint isolation probe v1")
mcp = MCPServer(
    "ChatGPT thread entrypoint isolation research",
    version="0.1.0",
    extensions=[apps],
)

if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host=os.environ.get("BIND_HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "3098")),
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
    )
