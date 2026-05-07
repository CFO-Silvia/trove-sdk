"""Tests for the MCP server surface.

The tools/prompts inside ``main()`` are closures over a captured client.
We test them by importing ``main`` and intercepting the FastMCP boot:
patch ``server.run`` to capture the registered handlers, then invoke
them against a mocked ``TroveClient`` to verify orchestration without
needing a real stdio loop or live network.
"""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import respx

from trove_sdk.models import FileInfo, WorkspaceBootstrap

# The MCP extra is optional; skip the whole module if not installed.
mcp = pytest.importorskip("mcp.server.fastmcp")

BASE = "https://api.trovefiles.dev"


def _boot_server(monkeypatch) -> dict:
    """Run trove_sdk.mcp.server.main() up to the point of stdio launch,
    returning the FastMCP instance and the captured handler maps."""
    monkeypatch.setenv("TROVE_API_KEY", "trove-sk-test")
    monkeypatch.setenv("TROVE_NAMESPACE", "test-ns")
    monkeypatch.setenv("TROVE_BASE_URL", BASE)

    captured: dict = {}

    real_init = mcp.FastMCP.__init__

    def capturing_init(self, name, **kwargs):
        real_init(self, name, **kwargs)
        captured["server"] = self
        captured["instructions"] = kwargs.get("instructions")

    with patch.object(mcp.FastMCP, "__init__", capturing_init), \
         patch.object(mcp.FastMCP, "run", lambda self: None):
        from trove_sdk.mcp import server as srv
        srv.main()

    return captured


@respx.mock
def test_mcp_server_exposes_bootstrap_tool_with_instructions(monkeypatch):
    """Server boots with handoff-aware instructions and a trove_bootstrap tool."""
    captured = _boot_server(monkeypatch)
    assert "trove_bootstrap" in captured["instructions"]
    assert "workspace/.trove/agent.md" in captured["instructions"]

    server = captured["server"]
    # FastMCP exposes registered tools via `_tool_manager._tools` (private but
    # stable enough for unit tests; the alternative is round-tripping JSON-RPC).
    tool_names = set(server._tool_manager._tools.keys())
    assert {"trove_exec", "trove_read", "trove_write",
            "trove_put_base64", "trove_bootstrap"} <= tool_names


@respx.mock
def test_trove_bootstrap_tool_returns_rendered_block(monkeypatch):
    """The tool wires through to client.bootstrap() and returns the rendered XML."""
    # Mock the three HTTP calls that bootstrap() composes.
    respx.get(f"{BASE}/v1/files").mock(
        return_value=httpx.Response(
            200,
            json={
                "entries": [
                    {"name": "data.csv", "path": "workspace/data.csv",
                     "is_dir": False, "size_bytes": 100,
                     "modified_at": "2026-05-07T20:00:00Z"},
                ],
                "truncated": False,
            },
        )
    )
    def content_handler(request):
        path = request.url.params.get("path")
        if path == "workspace/.trove/init.sh":
            return httpx.Response(404, json={"detail": "not found"})
        if path == "workspace/.trove/agent.md":
            return httpx.Response(
                200,
                json={"path": path, "size_bytes": 9, "modified_at": "t",
                      "encoding": "utf-8", "content": "carry on."},
            )
        return httpx.Response(404, json={"detail": "not found"})
    respx.get(f"{BASE}/v1/files/content").mock(side_effect=content_handler)

    captured = _boot_server(monkeypatch)
    bootstrap_tool = captured["server"]._tool_manager._tools["trove_bootstrap"]
    # FastMCP wraps the function but keeps the original on `.fn`.
    out = bootstrap_tool.fn()
    assert "<workspace>" in out and "</workspace>" in out
    assert "namespace: test-ns" in out
    assert "files: 1" in out
    assert "workspace/data.csv" in out
    assert "last_session: |" in out
    assert "carry on." in out


@respx.mock
def test_trove_bootstrap_tool_surfaces_trove_errors_as_strings(monkeypatch):
    """Errors are rendered as a human-readable string, not raised — the
    MCP protocol expects a tool result, not an exception."""
    respx.get(f"{BASE}/v1/files").mock(
        return_value=httpx.Response(401, json={"detail": "Invalid API key"})
    )

    captured = _boot_server(monkeypatch)
    bootstrap_tool = captured["server"]._tool_manager._tools["trove_bootstrap"]
    out = bootstrap_tool.fn()
    assert "trove error (HTTP 401)" in out
    assert "Invalid API key" in out


def test_workspace_bootstrap_is_directly_renderable_without_mcp():
    """Sanity: the renderer used by the MCP tool is a pure dataclass method
    so it works in unit tests without booting FastMCP at all."""
    bs = WorkspaceBootstrap(
        namespace="x", file_count=1, last_modified_at="t",
        recent_files=[FileInfo("a", "workspace/a", False, 5, "t")],
        init_script=None, agent_memory=None, truncated=False,
    )
    block = bs.as_system_prompt_block()
    assert block.startswith("<workspace>")
    assert "namespace: x" in block
