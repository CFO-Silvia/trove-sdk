"""Detect MCP-compatible clients and merge a Trove server entry into their config.

Each supported client stores MCP server configs in a JSON file:

    Claude Desktop  ~/Library/.../Claude/claude_desktop_config.json   (per-platform)
    Cursor          ~/.cursor/mcp.json
    Claude Code     <cwd>/.mcp.json                                   (project-scoped)

We merge into ``mcpServers.<name>`` (default name ``trove``) preserving any
other servers the user already configured. Existing trove entries are
overwritten — this is what makes ``trove mcp install`` idempotent.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

_DEFAULT_BASE_URL = "https://api.trovefiles.dev"


# ── Client config locations ──────────────────────────────────────────────────


def claude_desktop_config_path() -> Path:
    """Per-OS Claude Desktop config path. Returns the path even if missing —
    callers check existence themselves so we can offer helpful diagnostics."""
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        )
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def cursor_config_path() -> Path:
    return Path.home() / ".cursor" / "mcp.json"


def claude_code_config_path() -> Path:
    """Claude Code uses a project-scoped ``.mcp.json`` at the project root.

    We use the current working directory — the user is expected to ``cd`` into
    their project before running ``trove mcp install --client claude-code``.
    """
    return Path.cwd() / ".mcp.json"


@dataclass(frozen=True)
class ClientSpec:
    key: str               # CLI flag value, e.g. "claude-desktop"
    label: str             # Human-readable, e.g. "Claude Desktop"
    config_path: Callable[[], Path]
    auto_detect: bool      # Include in `detect_clients()` output


CLIENTS: dict[str, ClientSpec] = {
    "claude-desktop": ClientSpec(
        key="claude-desktop",
        label="Claude Desktop",
        config_path=claude_desktop_config_path,
        auto_detect=True,
    ),
    "cursor": ClientSpec(
        key="cursor",
        label="Cursor",
        config_path=cursor_config_path,
        auto_detect=True,
    ),
    "claude-code": ClientSpec(
        key="claude-code",
        label="Claude Code (project)",
        config_path=claude_code_config_path,
        # Project-scoped — never auto-install; user opts in explicitly so we
        # don't silently drop a .mcp.json into the wrong directory.
        auto_detect=False,
    ),
}


def detect_clients() -> list[str]:
    """Return the keys of clients whose config directory exists.

    We check the *parent* directory rather than the file itself: a freshly
    installed Claude Desktop doesn't ship the config file, but the dir is
    there. This way ``install`` works on a clean install.
    """
    found: list[str] = []
    for spec in CLIENTS.values():
        if not spec.auto_detect:
            continue
        try:
            if spec.config_path().parent.exists():
                found.append(spec.key)
        except OSError:
            continue
    return found


# ── Server entry construction ────────────────────────────────────────────────


def build_server_entry(
    api_key: str,
    namespace: str,
    *,
    base_url: str | None = None,
    python_executable: str | None = None,
) -> dict:
    """Build the MCP server entry to merge into a client config.

    Uses the absolute path of the running Python interpreter so the client
    finds the same env that has ``trove-sdk[mcp]`` installed — relying on
    PATH would break for Claude Desktop, which inherits a minimal env.
    """
    env = {"TROVE_API_KEY": api_key, "TROVE_NAMESPACE": namespace}
    if base_url and base_url != _DEFAULT_BASE_URL:
        env["TROVE_BASE_URL"] = base_url
    return {
        "command": python_executable or sys.executable,
        "args": ["-m", "trove_sdk.mcp.server"],
        "env": env,
    }


# ── Read / write the merged config ───────────────────────────────────────────


def _load_config(path: Path) -> dict:
    """Read an existing config file, or return an empty dict.

    Refuses to clobber a non-JSON file — that's almost certainly the user's
    work product and we'd rather error than overwrite it.
    """
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise RuntimeError(f"cannot read {path}: {e}") from e
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"existing {path} is not valid JSON ({e}); refusing to overwrite. "
            f"Fix the file or back it up and retry."
        ) from e
    if not isinstance(data, dict):
        raise RuntimeError(f"existing {path} is JSON but not an object; refusing to overwrite.")
    return data


def _atomic_write(path: Path, data: dict) -> None:
    """Write JSON to ``path`` via a sibling tempfile + rename.

    Avoids leaving a half-written config if the process dies mid-write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def install_for_client(
    client_key: str,
    entry: dict,
    *,
    server_name: str = "trove",
) -> Path:
    """Merge ``entry`` into the client's config under ``mcpServers.<server_name>``.

    Returns the path that was written. Existing servers under other names
    are left alone.
    """
    if client_key not in CLIENTS:
        raise ValueError(f"unknown client {client_key!r}; expected one of {sorted(CLIENTS)}")
    path = CLIENTS[client_key].config_path()
    data = _load_config(path)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
        data["mcpServers"] = servers
    servers[server_name] = entry
    _atomic_write(path, data)
    return path


def uninstall_for_client(
    client_key: str,
    *,
    server_name: str = "trove",
) -> tuple[bool, Path]:
    """Remove ``mcpServers.<server_name>`` from the client's config.

    Returns ``(removed, path)``. ``removed=False`` means there was no entry
    to remove — not an error condition for the caller.
    """
    if client_key not in CLIENTS:
        raise ValueError(f"unknown client {client_key!r}")
    path = CLIENTS[client_key].config_path()
    if not path.exists():
        return False, path
    data = _load_config(path)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or server_name not in servers:
        return False, path
    del servers[server_name]
    if not servers:
        data.pop("mcpServers", None)
    _atomic_write(path, data)
    return True, path


def status_for_client(
    client_key: str,
    *,
    server_name: str = "trove",
) -> Optional[dict]:
    """Return the existing server entry, or None if the client has none."""
    if client_key not in CLIENTS:
        return None
    path = CLIENTS[client_key].config_path()
    if not path.exists():
        return None
    try:
        data = _load_config(path)
    except RuntimeError:
        return None
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return None
    entry = servers.get(server_name)
    return entry if isinstance(entry, dict) else None
