"""Muse-side client package for the ChatGPT MCP Bridge (offline preparation).

Nothing in this package talks to a live ChatGPT conversation. The client is
disabled unless it is explicitly wired to a transport by operator code, and it
never retries an ambiguous enqueue attempt automatically.
"""
