"""MCP server exposing Trove as tools to AI clients.

Launched by clients (Claude Desktop, Cursor, Claude Code) as a stdio
subprocess. Configuration arrives via env vars written by ``trove mcp
install``:

    TROVE_API_KEY     (required)
    TROVE_NAMESPACE   (required)
    TROVE_BASE_URL    (optional; default https://api.trovefiles.dev)

We expose four tools deliberately: ``trove_exec`` is the universal hammer
(every preinstalled Unix tool reachable through one entry point),
``trove_read`` / ``trove_write`` / ``trove_put_base64`` skip the
shell-quoting tax for the common cases, and ``trove_bootstrap`` lets the
model orient itself at session start without a probing ``ls`` + ``cat``
turn. ``ls``, ``rm``, ``snapshot`` are intentionally absent — agents can
reach them through ``trove_exec`` without bloating the tool list, which
hurts model performance.

Server ``instructions`` nudge the model to call ``trove_bootstrap`` on
its first turn and to leave a handoff note before ending substantive
sessions, so the cross-session memory loop closes itself in practice.
The same orientation block is also exposed as the ``trove_orient`` MCP
prompt (slash-command in Claude Desktop) for human-triggered seeding.
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
    server = FastMCP(
        "trove",
        instructions=(
            "Trove gives you a persistent POSIX workspace under workspace/. "
            "Files survive between calls and across sessions. Real Unix "
            "tools (jq, awk, pdftotext, ffmpeg, python3) are preinstalled.\n\n"
            "On your FIRST turn of any new conversation, call "
            "trove_bootstrap() to see what files are present, what shell "
            "prelude is active, and what handoff note (if any) the previous "
            "session left at workspace/.trove/agent.md. This is much cheaper "
            "than probing with ls + cat and lets you pick up where you "
            "stopped instead of restarting from amnesia.\n\n"
            "Before ending a substantive session, write a short markdown "
            "handoff to workspace/.trove/agent.md via trove_write — what "
            "you learned, what you ruled out, where the cache lives. The "
            "next instance of you will see it through trove_bootstrap()."
        ),
    )

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

    @server.tool()
    def trove_bootstrap() -> str:
        """Orient yourself in the Trove workspace at session start.

        Returns a rendered <workspace> block summarizing:

        - the namespace and total non-metadata file count
        - the 10 most recently modified files (with paths and sizes)
        - the active init.sh prelude that runs before every shell command
        - the previous session's handoff note from workspace/.trove/agent.md

        Call this on your FIRST turn of any conversation. It replaces the
        usual "let me run ls and cat to figure out what's here" probing
        with a single round-trip and lets you pick up where the previous
        instance left off.

        To leave a handoff note for the next session yourself, write
        markdown to workspace/.trove/agent.md via trove_write before
        ending — there is no dedicated method, the path is the API.

        Returns:
            An XML-tagged <workspace>...</workspace> block ready to read.
            Empty namespaces render cleanly as ``files: 0 (empty)``.
        """
        try:
            bs = client.bootstrap()
        except TroveError as e:
            return f"trove error (HTTP {e.status_code}): {e}"
        return bs.as_system_prompt_block()

    @server.prompt()
    def trove_orient() -> str:
        """Show the current state of the Trove workspace.

        Surfaces recent files, the active init.sh, and the previous
        session's handoff note as a system-prompt-shaped block. Useful
        for manually seeding a new conversation, or mid-session when
        you want the model to re-orient.
        """
        try:
            bs = client.bootstrap()
        except TroveError as e:
            return f"trove error (HTTP {e.status_code}): {e}"
        return bs.as_system_prompt_block()

    server.run()


if __name__ == "__main__":
    main()
