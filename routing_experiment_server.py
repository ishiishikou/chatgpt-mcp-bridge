"""Isolated target=active/new experiment. Never imports the retired sender.

Importing, tools/list, show_* calls, and resources/read do not create a ledger.
Rendering a new auto resource WILL attempt a message after 15 seconds if the iframe remains running.
"""
import json
import os
from pathlib import Path

from mcp.server.apps import Apps, ResourceCsp
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context

import lifecycle_server as lifecycle
from routing_experiment_store import RoutingStore

URIS = {mode: f"ui://chatgpt-routing-experiment/auto-{mode}-v1.html"
        for mode in ("active", "new")}
apps = Apps()
store = RoutingStore()

# Register the original function, description, metadata, and bytes unchanged.
show_lifecycle_probe = lifecycle.show_lifecycle_probe
apps.tool(resource_uri=lifecycle.URI,
          description=lifecycle.mcp._tool_manager.list_tools()[0].description)(show_lifecycle_probe)
apps.add_html_resource(lifecycle.URI, lifecycle.HTML, title="MCP Apps lifecycle display-only v1")


@apps.tool(resource_uri=URIS["active"], description=(
    "SIDE EFFECT: rendering this UI automatically requests one fixed harmless user message "
    "after 15 seconds, target=active, send=true. Consumes the persistent mode/OS/visibility claim even "
    "if delivery is unknown. Teardown/capability failures skip. Use only for this experiment."))
def show_auto_active_probe(ctx: Context) -> str:
    return "AUTO routing experiment: active. Rendering starts a 15-second one-attempt timer."


@apps.tool(resource_uri=URIS["new"], description=(
    "SIDE EFFECT: rendering this UI automatically requests one fixed harmless user message "
    "in a NEW chat after 15 seconds, target=new, send=true. Consumes the persistent mode/OS "
    "claim even if delivery is unknown. Teardown/capability failures skip. Use only for this experiment."))
def show_auto_new_probe(ctx: Context) -> str:
    return "AUTO routing experiment: new. Rendering starts a 15-second one-attempt timer."


for mode, uri in URIS.items():
    apps.add_html_resource(
        uri, Path(__file__).with_name(f"routing-auto-{mode}-v1.html").read_text(encoding="utf-8"),
        title=f"Routing experiment AUTO {mode} v1",
        csp=ResourceCsp(connect_domains=[], resource_domains=[]),
    )

mcp = MCPServer("Isolated automatic target routing experiment", version="1.0.0", extensions=[apps])
APP_ONLY = {"ui": {"visibility": ["app"]}}


def claim_routing_probe(ctx: Context, mode: str, device: str, run_id: str, generated_at: str, phase: str = "visible") -> str:
    return json.dumps(store.claim(mode, device, run_id, generated_at, phase))


def report_routing_probe(ctx: Context, claim_token: str, outcome: str) -> str:
    return json.dumps(store.report(claim_token, outcome))


def record_routing_event(ctx: Context, mode: str, device: str, run_id: str,
                         event: str, visibility: str, elapsed_ms: int) -> str:
    return json.dumps(store.record(mode, device, run_id, event, visibility, elapsed_ms))


mcp.add_tool(claim_routing_probe, meta=APP_ONLY, description=(
    "App-only SIDE EFFECT: permanently consume one mode/OS/visibility slot, authorizing one automatic "
    "ui/message attempt. Never regrants, including a lost response. No recipient authentication."))
mcp.add_tool(report_routing_probe, meta=APP_ONLY, description=(
    "App-only: record one host result with its claim token; never resend or release a claim."))
mcp.add_tool(record_routing_event, meta=APP_ONLY, description=(
    "App-only: best-effort bounded lifecycle telemetry in the isolated experiment DB. No dispatch."))


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host=os.environ.get("BIND_HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", "3092")), streamable_http_path="/mcp",
            json_response=True, stateless_http=True)
