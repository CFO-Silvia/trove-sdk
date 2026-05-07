"""MCP server exposing Trove as tools to AI clients.

Launched by clients (Claude Desktop, Cursor, Claude Code) as a stdio
subprocess. Configuration arrives via env vars written by ``trove mcp
install``:

    TROVE_API_KEY     (required)
    TROVE_NAMESPACE   (required)
    TROVE_BASE_URL    (optional; default https://api.trovefiles.dev)

We expose three tools deliberately: ``trove_exec`` is the universal hammer
(every preinstalled Unix tool reachable through one entry point), and
``trove_read`` / ``trove_write`` skip the shell-quoting tax for the common
case. ``ls``, ``rm``, ``snapshot`` are intentionally absent — agents can
reach them through ``trove_exec`` without bloating the tool list, which
hurts model performance.
"""

from __future__ import annotations

import os
import sys

from .. import TroveClient, TroveError

_DEFAULT_BASE_URL = "https://api.trovefiles.dev"

_MISSING_EXTRA = (
    "trove-mcp requires the [mcp] extra. Install with:\n"
    "    pip install 'trove-sdk[mcp]'\n"
    "or\n"
    "    uv add 'trove-sdk[mcp]'"
)


def _build_client() -> TroveClient:
    api_key = os.environ.get("TROVE_API_KEY")
    namespace = os.environ.get("TROVE_NAMESPACE")
    base_url = os.environ.get("TROVE_BASE_URL", _DEFAULT_BASE_URL)
    if not api_key:
        print("trove-mcp: TROVE_API_KEY is not set", file=sys.stderr)
        sys.exit(2)
    if not namespace:
        print("trove-mcp: TROVE_NAMESPACE is not set", file=sys.stderr)
        sys.exit(2)
    return TroveClient(api_key=api_key, namespace=namespace, base_url=base_url)


def _format_exec(exit_code: int, stdout: str, stderr: str) -> str:
    """Render an ExecResult into a single string the model can parse.

    We include exit_code as the first line so the model can branch on success
    without us having to multiplex two MCP "content" blocks. stderr labelled
    explicitly so the model doesn't conflate diagnostic output with results.
    """
    parts = [f"[exit {exit_code}]"]
    if stdout:
        parts.append(stdout if stdout.endswith("\n") else stdout + "\n")
    if stderr:
        parts.append("--- stderr ---\n" + (stderr if stderr.endswith("\n") else stderr + "\n"))
    return "".join(parts) if len(parts) > 1 else parts[0]


def main() -> None:
    """Entry point for the ``trove-mcp`` console script."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        print(_MISSING_EXTRA, file=sys.stderr)
        sys.exit(1)

    client = _build_client()
    server = FastMCP("trove")

    @server.tool()
    def trove_exec(command: str, stdin: str | None = None) -> str:
        """Run a shell command in the Trove workspace.

        The workspace is a persistent POSIX filesystem isolated to this
        session's namespace. Files survive between calls. Real Unix tools are
        preinstalled: ``jq``, ``awk``, ``sed``, ``grep``, ``pdftotext``,
        ``ffmpeg``, ``imagemagick``, ``exiftool``, ``python3``, etc.

        Use this for anything you'd run in a shell — listing files, running
        scripts, transforming data, extracting text from PDFs, resizing
        images. Each call has a 30-second wall clock; long-running commands
        will be killed.

        Args:
            command: The shell command (e.g. ``ls workspace/``,
                ``jq .field workspace/data.json``, ``python3 workspace/x.py``).
            stdin: Optional UTF-8 text piped to the command's stdin.
                Max 1 MB. Use ``trove_write`` + redirect for larger inputs.

        Returns:
            ``[exit N]`` followed by stdout, then ``--- stderr ---`` and
            stderr if present.
        """
        try:
            r = client.exec_detailed(command, stdin=stdin)
        except TroveError as e:
            return f"trove error (HTTP {e.status_code}): {e}"
        return _format_exec(r.exit_code, r.stdout, r.stderr)

    @server.tool()
    def trove_read(path: str) -> str:
        """Read a UTF-8 text file from the Trove workspace.

        For binary files (images, PDFs, audio) or files larger than 1 MB,
        use ``trove_exec`` with ``cat``, ``head``, ``pdftotext``, etc.

        Args:
            path: The file path inside the workspace
                (e.g. ``workspace/notes.md``).
        """
        try:
            return client.read_text(path)
        except TroveError as e:
            return f"trove error (HTTP {e.status_code}): {e}"

    @server.tool()
    def trove_write(path: str, content: str) -> str:
        """Write a UTF-8 text file in the Trove workspace (creates or overwrites).

        Args:
            path: Destination path (e.g. ``workspace/plan.md``).
                Parent directories are created as needed.
            content: Full file contents as UTF-8 text. For binary files
                (PDFs, images, audio) use ``trove_put_base64`` instead.

        Returns:
            Confirmation including the resolved path and byte count.
        """
        try:
            r = client.write(path, content)
        except TroveError as e:
            return f"trove error (HTTP {e.status_code}): {e}"
        return f"wrote {r.path} ({r.size_bytes} bytes)"

    @server.tool()
    def trove_put_base64(path: str, content_b64: str) -> str:
        """Write a binary file from base64 content (PDFs, images, audio).

        One call instead of the ``trove_write`` + ``trove_exec | base64 -d``
        dance — and no shell-quoting hazard.

        Args:
            path: Destination path (e.g. ``workspace/report.pdf``).
            content_b64: Standard base64 of the file's bytes (no data:
                URL prefix, whitespace tolerated).

        Returns:
            Confirmation including the resolved path and decoded byte count.
        """
        import base64 as _b64
        import binascii as _bx
        try:
            data = _b64.b64decode(content_b64, validate=False)
        except (_bx.Error, ValueError) as e:
            return f"invalid base64: {e}"
        try:
            r = client.upload(path, data)
        except TroveError as e:
            return f"trove error (HTTP {e.status_code}): {e}"
        return f"wrote {r.path} ({r.size_bytes} bytes)"

    server.run()


if __name__ == "__main__":
    main()
