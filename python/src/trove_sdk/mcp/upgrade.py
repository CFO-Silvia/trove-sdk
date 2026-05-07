"""Upgrade orchestration for the trove-sdk MCP install.

The install writes the absolute path of ``sys.executable`` into each
client's config — see :func:`build_server_entry`. That makes installation
robust (Claude Desktop's minimal env can't break PATH lookups) but means
the user can't update Trove with a single ``pip install --upgrade`` from
their shell unless they happen to be in the same env. Most aren't:
``trove mcp install`` is typically run via ``uv tool`` or ``pipx`` for
isolation.

This module detects the env kind from the Python path, finds the right
upgrade command, and queries PyPI for the current latest. The CLI
``trove mcp upgrade`` ties them together; ``trove mcp status`` uses
:func:`installed_version_for_python` and :func:`latest_pypi_version`
to flag stale installs.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

from .install import CLIENTS, status_for_client

PYPI_URL = "https://pypi.org/pypi/trove-sdk/json"


# ── Env detection ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EnvKind:
    """How a Python install was provisioned, used to pick an upgrade command.

    ``label`` is what we surface in CLI output; ``recipe`` is a short hint
    string for logs. ``key`` is stable for programmatic use.
    """
    key: str
    label: str
    recipe: str


UV_TOOL = EnvKind("uv-tool", "uv tool", "uv tool upgrade trove-sdk")
PIPX = EnvKind("pipx", "pipx", "pipx upgrade trove-sdk")
PIP = EnvKind("pip", "pip (in-place)", "<python> -m pip install --upgrade 'trove-sdk[mcp]'")
UNKNOWN = EnvKind("unknown", "unknown", "")


def detect_env_kind(python_path: Path | str) -> EnvKind:
    """Infer how a Python interpreter was installed from its on-disk path.

    Detection is path-pattern based — fast and works without spawning the
    interpreter. uv and pipx both place tool installs under predictable
    subtrees; everything else we treat as a regular pip-managed env (could
    be system Python, a venv, conda, anything else — they all upgrade the
    same way: ``<that-python> -m pip install --upgrade``).
    """
    if not python_path:
        return UNKNOWN
    p = Path(str(python_path)).as_posix().lower()
    if "/uv/tools/" in p or "/uv/tool/" in p:
        return UV_TOOL
    if "/pipx/venvs/" in p:
        return PIPX
    return PIP


def upgrade_command(python_path: Path | str, kind: EnvKind) -> list[str]:
    """Return the argv list that upgrades trove-sdk in the given env.

    For pip-managed envs the command runs the *target* Python explicitly —
    ``<that-python> -m pip install --upgrade``. We keep the ``[mcp]`` extra
    on the command so users with the bare package don't lose the MCP server
    silently after upgrade.

    uv-tool and pipx keep extras already declared on their install line, so
    we don't re-pass them — re-passing them is technically harmless but
    makes the CLI output noisier.
    """
    if kind is UV_TOOL:
        return ["uv", "tool", "upgrade", "trove-sdk"]
    if kind is PIPX:
        return ["pipx", "upgrade", "trove-sdk"]
    # PIP / UNKNOWN — best-effort. UNKNOWN paths are still likely pip-style.
    return [str(python_path), "-m", "pip", "install", "--upgrade", "trove-sdk[mcp]"]


# ── Version queries ──────────────────────────────────────────────────────────


def installed_version_for_python(python_path: Path | str, *, timeout: float = 3.0) -> Optional[str]:
    """Ask the target Python what version of trove-sdk it has installed.

    Returns ``None`` if the Python doesn't exist, doesn't have trove-sdk,
    or refuses to run for any reason — callers shouldn't crash on a
    busted install path; they should display the absence.
    """
    try:
        r = subprocess.run(
            [
                str(python_path),
                "-c",
                "import importlib.metadata as m; "
                "print(m.version('trove-sdk'))",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    out = (r.stdout or "").strip()
    return out or None


_LATEST_CACHE: dict[str, str] = {}


def latest_pypi_version(*, timeout: float = 5.0, cache: bool = True) -> Optional[str]:
    """Return the latest trove-sdk version on PyPI, or None if offline.

    Cached per-process so a single ``status`` call doesn't fan out to PyPI
    once per detected client. Callers that want a fresh fetch (release
    smoke-tests, mostly) pass ``cache=False``.
    """
    if cache and "v" in _LATEST_CACHE:
        return _LATEST_CACHE["v"]
    try:
        resp = httpx.get(PYPI_URL, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, json.JSONDecodeError):
        return None
    version = data.get("info", {}).get("version")
    if version and cache:
        _LATEST_CACHE["v"] = version
    return version


# ── Discovery: what Python is each client actually invoking? ─────────────────


@dataclass
class ClientInstall:
    """One client's view of where Trove is installed and how to upgrade it."""
    client_key: str
    label: str
    python_path: Optional[Path]   # the `command` from the config, if any
    env_kind: EnvKind
    installed_version: Optional[str]


def discover_installs(*, server_name: str = "trove") -> list[ClientInstall]:
    """For each known client, read its config and report what it's running.

    Skips clients that aren't configured. Doesn't hit PyPI — the caller does
    that once via :func:`latest_pypi_version` and compares.
    """
    out: list[ClientInstall] = []
    for key, spec in CLIENTS.items():
        entry = status_for_client(key, server_name=server_name)
        if entry is None:
            continue
        cmd = entry.get("command")
        py = Path(cmd) if isinstance(cmd, str) and cmd else None
        kind = detect_env_kind(py) if py else UNKNOWN
        version = installed_version_for_python(py) if py else None
        out.append(
            ClientInstall(
                client_key=key,
                label=spec.label,
                python_path=py,
                env_kind=kind,
                installed_version=version,
            )
        )
    return out


# ── Run an upgrade ───────────────────────────────────────────────────────────


@dataclass
class UpgradeResult:
    """Outcome of one upgrade attempt for a given install."""
    install: ClientInstall
    ran: bool
    succeeded: bool
    new_version: Optional[str]
    message: str  # human-readable status (success summary or error reason)


def upgrade_install(install: ClientInstall, *, dry_run: bool = False) -> UpgradeResult:
    """Run the upgrade command for one ``ClientInstall`` and report what happened.

    Network and tool errors (uv/pipx not on PATH, PyPI unreachable, package
    not found) are caught and surfaced via the ``UpgradeResult.message`` so
    the CLI can format them — we never let a single failed client take down
    the whole upgrade run.
    """
    if install.python_path is None:
        return UpgradeResult(install, False, False, None,
                             "no command path in config (was this client configured by a different tool?)")

    cmd = upgrade_command(install.python_path, install.env_kind)
    if dry_run:
        return UpgradeResult(install, False, True, None, f"would run: {' '.join(cmd)}")

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except FileNotFoundError:
        # Most common when uv-tool was used to install but uv isn't on PATH
        # in the shell that's now running `trove mcp upgrade`.
        return UpgradeResult(install, False, False, None,
                             f"`{cmd[0]}` not found on PATH — run `{install.env_kind.recipe}` manually")
    except subprocess.TimeoutExpired:
        return UpgradeResult(install, True, False, None, "upgrade timed out after 180s")

    if r.returncode != 0:
        # Surface the last few lines of stderr so users have something to act on.
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-3:]
        return UpgradeResult(install, True, False, None,
                             "upgrade failed: " + " | ".join(tail) if tail else "upgrade failed (no output)")

    new = installed_version_for_python(install.python_path)
    if new and install.installed_version and new != install.installed_version:
        msg = f"upgraded {install.installed_version} → {new}"
    elif new:
        msg = f"already at {new}"
    else:
        msg = "upgrade completed (could not re-read version)"
    return UpgradeResult(install, True, True, new, msg)
