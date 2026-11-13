"""Isolated read-only MCP Apps lifecycle probe. No dispatch tools or ledger access.

Use this server instead of server.py for cross-chat presentation experiments.
"""
import os
from pathlib import Path

from mcp.server.apps import Apps
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context

URI = "ui://chatgpt-event-probe/lifecycle-display-v1.html"
HTML = Path(__file__).with_name("lifecycle-display-v1.html").read_text(encoding="utf-8")

apps = Apps()

@apps.tool(
    resource_uri=URI,
    description="Display-only MCP Apps lifecycle diagnostic; no sending, no server-tool callbacks, no outbound communication."
)
def show_lifecycle_probe(ctx: Context) -> str:
    return "Lifecycle v1 display-only diagnostic. No messages or server tools are called."

apps.add_html_resource(URI, HTML, title="MCP Apps lifecycle display-only v1")
mcp = MCPServer("ChatGPT lifecycle display-only probe", version="1.0.0", extensions=[apps])

if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host=os.environ.get("BIND_HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "3092")),
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
    )
