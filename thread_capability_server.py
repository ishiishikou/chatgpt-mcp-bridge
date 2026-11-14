"""Isolated, display-only thread-host capability probe.

No sender, ledger, external event data, identity inference or draft requests.
Advertising thread entrypoints does not attest a ChatGPT conversation ID.
"""
import os
from pathlib import Path

from mcp.server.apps import Apps
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context

BUILD = "capdiag-20261114-v1"
URI = "ui://chatgpt-mcp-bridge/thread-capabilities-20261114-v1.html"
HTML = Path(__file__).with_name("thread-capabilities-20261114-v1.html").read_text(encoding="utf-8")
apps = Apps()


@apps.tool(
    resource_uri=URI,
    meta={"openai/ui": {"entrypoints": [{"type": "thread"}]}},
    description=(
        "Open an isolated, display-only diagnostics UI for advertised host capabilities. "
        "Does not send, draft, dispatch, claim, read events, or verify a ChatGPT thread ID."
    ),
)
def show_thread_capability_probe(ctx: Context) -> str:
    return (
        f"Build {BUILD}. Strictly display-only; no user-message or draft requests. "
        "The client UI has an independent per-mount marker. Neither marker identifies the recipient chat."
    )


apps.add_html_resource(URI, HTML, title=f"Thread capabilities {BUILD}")
mcp = MCPServer("Thread host capability display-only diagnosis",
                version="0.1.0", extensions=[apps])

if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host=os.environ.get("BIND_HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "3099")),
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
    )