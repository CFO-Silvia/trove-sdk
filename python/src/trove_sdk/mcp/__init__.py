"""MCP server for Trove — exposes the workspace to AI clients (Claude Desktop,
Cursor, Claude Code) via the Model Context Protocol.

The server itself lives in :mod:`trove_sdk.mcp.server` and is launched by the
``trove-mcp`` console script. The :mod:`trove_sdk.mcp.install` module handles
client-config detection and merging, used by ``trove mcp install``.
"""
